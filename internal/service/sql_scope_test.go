package service

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"os"
	"reflect"
	"strings"
	"testing"

	"ragflow/internal/engine"
	"ragflow/internal/engine/types"
	"ragflow/internal/entity"

	_ "github.com/glebarez/go-sqlite"
)

type projectionMetadataEngine struct {
	engine.DocEngine
	calls int
}

func (e *projectionMetadataEngine) SearchMetadata(context.Context, *types.SearchMetadataRequest) (*types.SearchMetadataResult, error) {
	e.calls++
	return &types.SearchMetadataResult{MetadataRecords: []map[string]interface{}{
		{"id": "private-doc", "meta_fields": map[string]interface{}{"secret": "PRIVATE_SENTINEL"}},
	}}, nil
}

func TestBuildSQLReferenceRejectsForeignMetadata(t *testing.T) {
	allowed := []string{"11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"}
	foreign := "33333333-3333-4333-8333-333333333333"
	for _, column := range []string{"kb_id", "kb_id_kwd", "KB_ID_KWD"} {
		t.Run(column, func(t *testing.T) {
			query, err := scopeRetrievalSQL(fmt.Sprintf("SELECT 'private-doc' AS doc_id, 'Private' AS docnm_kwd, '%s' AS %s, amount FROM ragflow_tenant", foreign, column), "ragflow_tenant", "elasticsearch", allowed)
			if err != nil {
				t.Fatal(err)
			}
			db, err := sql.Open("sqlite", ":memory:")
			if err != nil {
				t.Fatal(err)
			}
			defer db.Close()
			if _, err := db.Exec("CREATE TABLE ragflow_tenant(kb_id TEXT, amount INT); INSERT INTO ragflow_tenant VALUES ('" + allowed[0] + "',1)"); err != nil {
				t.Fatal(err)
			}
			var docID, docName, kbID string
			var amount int
			if err := db.QueryRow(query).Scan(&docID, &docName, &kbID, &amount); err != nil {
				t.Fatal(err)
			}
			rows := []map[string]interface{}{{"doc_id": docID, "docnm_kwd": docName, column: kbID, "amount": amount}}
			_, ref := (&ChatPipelineService{}).buildSQLReference(context.Background(), nil, "ragflow_tenant", query, rows, "", "elasticsearch", []*entity.Knowledgebase{{ID: allowed[0]}, {ID: allowed[1]}}, nil)
			chunks := ref["chunks"].([]map[string]interface{})
			metadataEngine := &projectionMetadataEngine{}
			(&MetadataService{docEngine: metadataEngine}).EnrichChunksWithDocMetadata(chunks, "tenant", nil)
			if len(chunks) == 0 || metadataEngine.calls != 0 {
				t.Fatalf("foreign metadata lookup: chunks=%v calls=%d", chunks, metadataEngine.calls)
			}
			for _, chunk := range chunks {
				if chunk["kb_id"] != nil || chunk["document_metadata"] != nil {
					t.Fatalf("untrusted source escaped scope: %v", chunk)
				}
			}
			if got := chunkKBIDForDoc(map[string]interface{}{column: allowed[1]}, allowed); got != allowed[1] {
				t.Fatalf("authorized source lost: %q", got)
			}
		})
	}
}

func TestSQLRetrievalScope(t *testing.T) {
	raw, err := os.ReadFile("../../test/fixtures/sql_retrieval_scope.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Name   string    `json:"name"`
		SQL    string    `json:"sql"`
		Engine string    `json:"engine"`
		Table  string    `json:"table"`
		KBIDs  *[]string `json:"kb_ids"`
		Rows   []string  `json:"rows"`
		Reject bool      `json:"reject"`
	}
	if err := json.Unmarshal(raw, &cases); err != nil {
		t.Fatal(err)
	}
	for _, tc := range cases {
		t.Run(tc.Name, func(t *testing.T) {
			ids := []string{"11111111-1111-4111-8111-111111111111"}
			if tc.KBIDs != nil {
				ids = *tc.KBIDs
			}
			if tc.Table == "" {
				tc.Table = "ragflow_tenant"
			}
			if tc.Engine == "" {
				tc.Engine = "elasticsearch"
			}
			query, err := scopeRetrievalSQL(tc.SQL, tc.Table, tc.Engine, ids)
			if tc.Reject {
				if err == nil {
					t.Fatalf("unsafe SQL accepted: %s", query)
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			db, err := sql.Open("sqlite", ":memory:")
			if err != nil {
				t.Fatal(err)
			}
			defer db.Close()
			// SQLite's json_extract has the same scalar semantics for this corpus.
			query = strings.ReplaceAll(query, "json_extract_string", "json_extract")
			_, err = db.Exec(`CREATE TABLE ragflow_tenant(doc_id TEXT, kb_id TEXT, amount INT, docnm TEXT, chunk_data TEXT);
INSERT INTO ragflow_tenant VALUES ('allowed','11111111-1111-4111-8111-111111111111',1,'Desk','{"Name":"Desk"}'),('private','22222222-2222-4222-8222-222222222222',2,'Private','{"Name":"Private"}');
CREATE TABLE ragflow_tenant_11111111111141118111111111111111 AS SELECT * FROM ragflow_tenant WHERE amount=1;`)
			if err != nil {
				t.Fatal(err)
			}
			rows, err := db.Query(query)
			if err != nil {
				t.Fatalf("%s: %v", query, err)
			}
			defer rows.Close()
			got := []string{}
			for rows.Next() {
				var value interface{}
				if err := rows.Scan(&value); err != nil {
					t.Fatal(err)
				}
				got = append(got, fmt.Sprint(value))
			}
			if err := rows.Err(); err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(got, tc.Rows) {
				t.Fatalf("rows=%v want=%v SQL=%s", got, tc.Rows, query)
			}
		})
	}
}

func TestRetrievalSourceSQL(t *testing.T) {
	for _, tail := range []string{"", " WHERE t.amount=1 OR t.amount=2", " WHERE t.docnm_kwd='group by order by limit' OR t.amount=1 GROUP BY t.kb_id HAVING COUNT(*)>0"} {
		query, err := retrievalSourceSQL("SELECT COUNT(*) FROM ragflow_tenant AS t"+tail, "ragflow_tenant", "elasticsearch", []string{"11111111-1111-4111-8111-111111111111"})
		if err != nil {
			t.Fatal(err)
		}
		db, err := sql.Open("sqlite", ":memory:")
		if err != nil {
			t.Fatal(err)
		}
		_, err = db.Exec(`CREATE TABLE ragflow_tenant(doc_id TEXT, docnm_kwd TEXT, kb_id TEXT, amount INT);
INSERT INTO ragflow_tenant VALUES ('allowed','Desk','11111111-1111-4111-8111-111111111111',1),('private','Private','22222222-2222-4222-8222-222222222222',2);`)
		if err != nil {
			db.Close()
			t.Fatal(err)
		}
		rows, err := db.Query(query)
		if err != nil {
			db.Close()
			t.Fatal(err)
		}
		var ids []string
		for rows.Next() {
			var id, name string
			if err := rows.Scan(&id, &name); err != nil {
				t.Fatal(err)
			}
			ids = append(ids, id)
		}
		if err := rows.Err(); err != nil {
			t.Fatal(err)
		}
		rows.Close()
		db.Close()
		if !reflect.DeepEqual(ids, []string{"allowed"}) {
			t.Fatalf("citation scope: %v in %s", ids, query)
		}
	}
}
