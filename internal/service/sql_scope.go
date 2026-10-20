package service

import (
	"fmt"
	"strings"

	sql "github.com/rqlite/sql"
)

// scopedRetrievalSelect admits one SELECT over the trusted index. Scope is ANDed
// with the entire existing predicate; text supplied by the model is never proof
// of authorization. Unsupported query shapes fail before reaching the engine.
func scopedRetrievalSelect(text, tableName, engineName string, kbIDs []string) (*sql.SelectStatement, error) {
	if len(kbIDs) == 0 {
		return nil, fmt.Errorf("SQL retrieval requires an authorized knowledge base")
	}
	for _, id := range kbIDs {
		if !isValidUUID(id) {
			return nil, fmt.Errorf("invalid SQL retrieval knowledge base")
		}
	}
	if engineName == "infinity" && len(kbIDs) != 1 {
		return nil, fmt.Errorf("Infinity SQL retrieval requires exactly one knowledge base")
	}
	statements, err := sql.NewParser(strings.NewReader(text)).ParseStatements()
	if err != nil || len(statements) != 1 {
		return nil, fmt.Errorf("SQL retrieval requires a single SELECT")
	}
	query, ok := statements[0].(*sql.SelectStatement)
	if !ok || query.WithClause != nil || query.Compound != nil || len(query.ValueLists) != 0 {
		return nil, fmt.Errorf("unsupported retrieval SQL statement")
	}
	table, ok := query.Source.(*sql.QualifiedTableName)
	if !ok || table.Schema != nil || table.Name.Name != tableName {
		return nil, fmt.Errorf("SQL retrieval must read only the authorized table")
	}
	if _, err := sql.Walk(&retrievalSQLVisitor{query: query}, query); err != nil {
		return nil, err
	}
	if engineName != "infinity" {
		parts := make([]string, len(kbIDs))
		for i, id := range kbIDs {
			parts[i] = "'" + id + "'"
		}
		column := "kb_id"
		if table.Alias != nil {
			column = table.Alias.String() + "." + column
		}
		predicate, err := sql.ParseExprString(column + " IN (" + strings.Join(parts, ", ") + ")")
		if err != nil {
			return nil, err
		}
		if query.WhereExpr != nil {
			predicate = &sql.BinaryExpr{X: predicate, Op: sql.AND, Y: &sql.ParenExpr{X: query.WhereExpr}}
		}
		query.WhereExpr = predicate
	}
	return query, nil
}

func scopeRetrievalSQL(text, tableName, engineName string, kbIDs []string) (string, error) {
	query, err := scopedRetrievalSelect(text, tableName, engineName, kbIDs)
	if err != nil {
		return "", err
	}
	return query.String(), nil
}

func retrievalSourceSQL(text, tableName, engineName string, kbIDs []string) (string, error) {
	query, err := scopedRetrievalSelect(text, tableName, engineName, kbIDs)
	if err != nil {
		return "", err
	}
	table := query.Source.(*sql.QualifiedTableName)
	names := []string{"doc_id", expectedDocNameColumn(engineName)}
	if len(kbIDs) > 1 {
		names = append(names, "kb_id")
	}
	query.Columns = nil
	for _, name := range names {
		var expr sql.Expr = &sql.Ident{Name: name}
		if table.Alias != nil {
			expr = &sql.QualifiedRef{Table: table.Alias.Clone(), Column: &sql.Ident{Name: name}}
		}
		query.Columns = append(query.Columns, &sql.ResultColumn{Expr: expr})
	}
	query.GroupByExprs = nil
	query.HavingExpr = nil
	query.Distinct = sql.Pos{}
	query.OrderingTerms = nil
	query.Windows = nil
	query.OffsetExpr = nil
	query.LimitExpr = &sql.NumberLit{Value: "20"}
	return query.String(), nil
}

type retrievalSQLVisitor struct {
	query *sql.SelectStatement
}

func (v *retrievalSQLVisitor) Visit(node sql.Node) (sql.Visitor, sql.Node, error) {
	switch n := node.(type) {
	case *sql.SelectStatement:
		if n != v.query {
			return nil, nil, fmt.Errorf("nested retrieval SQL is not supported")
		}
	case *sql.Call:
		if n.Schema != nil {
			return nil, nil, fmt.Errorf("qualified retrieval SQL function is not supported")
		}
		switch strings.ToLower(n.Name.Name) {
		case "count", "sum", "avg", "min", "max", "coalesce", "nullif", "round", "abs", "lower", "upper", "length", "substr", "substring", "trim", "concat", "json_extract_string", "json_extract_isnull":
		default:
			return nil, nil, fmt.Errorf("unsupported retrieval SQL function")
		}
	}
	return v, node, nil
}

func (v *retrievalSQLVisitor) VisitEnd(node sql.Node) (sql.Node, error) {
	return node, nil
}
