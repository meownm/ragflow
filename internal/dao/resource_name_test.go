package dao

import (
	"errors"
	"fmt"
	"testing"

	"github.com/jackc/pgx/v5/pgconn"
	"gorm.io/gorm"
	"ragflow/internal/entity"
)

func TestResourceNameConflictWhitelist(t *testing.T) {
	for _, index := range []string{DialogNameIndex, CanvasNameIndex, KnowledgebaseNameIndex} {
		for _, tc := range []struct {
			err      error
			expected bool
		}{
			{nil, false},
			{gorm.ErrDuplicatedKey, false},
			{&pgconn.PgError{Code: "23505", ConstraintName: index}, true},
			{fmt.Errorf("wrapped: %w", &pgconn.PgError{Code: "23505", ConstraintName: index}), true},
			{&pgconn.PgError{Code: "23505", ConstraintName: "primary_key"}, false},
			{&pgconn.PgError{Code: "23503", ConstraintName: index}, false},
		} {
			if got := IsResourceNameConflict(tc.err, index); got != tc.expected {
				t.Fatalf("index=%s err=%v got=%v", index, tc.err, got)
			}
		}
	}
	if IsResourceNameConflict(&pgconn.PgError{Code: "23505", ConstraintName: "primary_key"}, "primary_key") {
		t.Fatal("non-name constraint accepted")
	}
	if IsResourceNameConflict(&pgconn.PgError{Code: "23505", ConstraintName: CanvasNameIndex}, DialogNameIndex) {
		t.Fatal("wrong resource accepted")
	}
}

func TestResourceNameWriteDoesNotChangeGlobalErrorTranslation(t *testing.T) {
	db := setupUserCanvasTestDB(t)
	write := resourceNameWrite(db)
	if !db.Config.TranslateError || write.Config.TranslateError || db.Config == write.Config {
		t.Fatal("write changed global config")
	}
	row := &entity.UserCanvas{ID: "same", UserID: "owner", Title: stringPtr("Title")}
	if err := write.Create(row).Error; err != nil {
		t.Fatal(err)
	}
	err := resourceNameWrite(db).Create(row).Error
	if err == nil || errors.Is(err, gorm.ErrDuplicatedKey) || !db.Config.TranslateError {
		t.Fatalf("expected untranslated local write error: %v", err)
	}
}

func TestResourceNameQueriesRespectOwnerCategoryStatusAndOwnID(t *testing.T) {
	db := setupUserCanvasTestDB(t)
	pushDB(t, db)
	if err := db.AutoMigrate(&entity.Chat{}, &entity.Knowledgebase{}); err != nil {
		t.Fatal(err)
	}
	for _, row := range []any{
		&entity.UserCanvas{ID: "agent", UserID: "owner", Title: stringPtr("  Existing  "), CanvasCategory: "agent_canvas", Permission: "me"},
		&entity.Chat{ID: "chat", TenantID: "owner", Name: stringPtr("  Existing  "), Status: stringPtr("1"), LLMSetting: entity.JSONMap{}, PromptConfig: entity.JSONMap{}, KBIDs: entity.JSONSlice{}},
		&entity.Chat{ID: "deleted-chat", TenantID: "owner", Name: stringPtr("Deleted"), Status: stringPtr("0"), LLMSetting: entity.JSONMap{}, PromptConfig: entity.JSONMap{}, KBIDs: entity.JSONSlice{}},
		&entity.Knowledgebase{ID: "kb", TenantID: "owner", Name: "  Existing  ", Status: stringPtr("1")},
		&entity.Knowledgebase{ID: "deleted-kb", TenantID: "owner", Name: "Deleted", Status: stringPtr("0")},
	} {
		if err := db.Create(row).Error; err != nil {
			t.Fatal(err)
		}
	}
	canvas := NewUserCanvasDAO()
	if row, err := canvas.GetByUserAndTitle("owner", "existing", "agent_canvas"); err != nil || row == nil {
		t.Fatalf("case collision missed: %v", err)
	}
	for _, args := range [][]string{{"peer", "existing", "agent_canvas"}, {"owner", "existing", "dataflow_canvas"}} {
		if row, err := canvas.GetByUserAndTitle(args[0], args[1], args[2]); err != nil || row != nil {
			t.Fatalf("foreign namespace matched: %v", err)
		}
	}
	if row, err := canvas.GetByUserAndTitle("owner", "existing", "agent_canvas", "agent"); err != nil || row != nil {
		t.Fatalf("self matched: %v", err)
	}
	chat := NewChatDAO()
	if exists, err := chat.ExistsByNameTenantStatus("existing", "owner", "1"); err != nil || !exists {
		t.Fatalf("chat missed: %v", err)
	}
	for _, args := range [][]string{{"existing", "peer"}, {"Deleted", "owner"}} {
		if exists, err := chat.ExistsByNameTenantStatus(args[0], args[1], "1"); err != nil || exists {
			t.Fatalf("chat scope leak: %v", err)
		}
	}
	if exists, err := chat.ExistsByNameTenantStatus("existing", "owner", "1", "chat"); err != nil || exists {
		t.Fatalf("chat self matched: %v", err)
	}
	kb := NewKnowledgebaseDAO()
	if row, err := kb.GetByName("existing", "owner"); err != nil || row == nil {
		t.Fatalf("kb missed: %v", err)
	}
	for _, args := range [][]string{{"existing", "peer"}, {"Deleted", "owner"}} {
		if _, err := kb.GetByName(args[0], args[1]); !errors.Is(err, gorm.ErrRecordNotFound) {
			t.Fatalf("kb scope leak: %v", err)
		}
	}
}
