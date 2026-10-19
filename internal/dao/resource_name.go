package dao

import (
	"errors"

	"github.com/jackc/pgx/v5/pgconn"
	"gorm.io/gorm"
)

const (
	DialogNameIndex        = "dialog_owner_name_unique"
	CanvasNameIndex        = "user_canvas_owner_title_unique"
	KnowledgebaseNameIndex = "knowledgebase_owner_name_unique"
)

// IsResourceNameConflict recognizes only the expected resource's name index.
func IsResourceNameConflict(err error, index string) bool {
	switch index {
	case DialogNameIndex, CanvasNameIndex, KnowledgebaseNameIndex:
	default:
		return false
	}
	var conflict *pgconn.PgError
	return errors.As(err, &conflict) && conflict.Code == "23505" && conflict.ConstraintName == index
}

// resourceNameWrite retains PostgreSQL constraint names on these writes without
// changing error translation for other database consumers. Session copies Config.
func resourceNameWrite(db *gorm.DB) *gorm.DB {
	write := db.Session(&gorm.Session{})
	write.Config.TranslateError = false
	return write
}
