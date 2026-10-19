package service

import (
	"context"
	"errors"
	"strings"
	"testing"
	"unicode/utf8"

	"github.com/jackc/pgx/v5/pgconn"
	"gorm.io/gorm"
	"ragflow/internal/common"
	"ragflow/internal/dao"
	"ragflow/internal/entity"
)

func TestAgentNameGuardDoesNotQueryPrivateNamesForMembers(t *testing.T) {
	// No DAO is supplied: any name lookup would panic, proving that the guard
	// permits unchanged autosaves and denies renames before consulting storage.
	svc := &AgentService{}
	canvas := &entity.UserCanvas{ID: "shared", UserID: "owner", Title: sptr("Public"), CanvasCategory: "agent_canvas", Permission: "team"}
	for _, patch := range []map[string]interface{}{
		{"title": "Private secret"}, {"title": "Available"}, {"canvas_category": "dataflow_canvas"}, {"permission": "me"},
	} {
		if _, err := svc.prepareAgentNameUpdates(canvas, "member", patch); !errors.Is(err, ErrAgentNotOwner) {
			t.Fatalf("rename not denied uniformly: %v", err)
		}
	}
	updates, err := svc.prepareAgentNameUpdates(canvas, "member", map[string]interface{}{"title": " Public ", "canvas_category": "agent_canvas", "permission": "team"})
	if err != nil || len(updates) != 0 {
		t.Fatalf("unchanged autosave consulted namespace: %v %v", updates, err)
	}
}

func TestAgentNamesCreateUpdateAndPublishShareRules(t *testing.T) {
	setupAgentSessionServiceTest(t)
	svc := NewAgentService()
	create := func(owner, title, category string) (*entity.UserCanvas, common.ErrorCode, error) {
		return svc.CreateAgent(context.Background(), &CreateAgentRequest{UserID: owner, Title: &title, CanvasCategory: category, DSL: entity.JSONMap{}})
	}
	first, code, err := create("owner", "  Existing  ", "")
	if err != nil || code != common.CodeSuccess || first.Title == nil || *first.Title != "Existing" {
		t.Fatalf("create failed: %v", err)
	}
	if _, code, err := create("owner", "existing", "agent_canvas"); err == nil || code != common.CodeDataError {
		t.Fatalf("case duplicate allowed: %v", err)
	}
	for _, args := range [][]string{{"peer", "existing", "agent_canvas"}, {"owner", "existing", "dataflow_canvas"}} {
		if _, code, err := create(args[0], args[1], args[2]); err != nil || code != common.CodeSuccess {
			t.Fatalf("independent namespace rejected: %v", err)
		}
	}
	for _, title := range []string{"   ", strings.Repeat("Я", 256)} {
		if _, _, err := create("owner", title, "agent_canvas"); err == nil {
			t.Fatal("invalid agent title accepted")
		}
	}
	second, _, err := create("owner", strings.Repeat("Я", 255), "agent_canvas")
	if err != nil {
		t.Fatal(err)
	}
	if err := svc.UpdateAgent(context.Background(), "owner", second.ID, map[string]interface{}{"title": "EXISTING"}); err == nil {
		t.Fatal("update duplicate accepted")
	}
	if _, err := svc.PublishAgent(context.Background(), "owner", second.ID, &PublishAgentRequest{Title: sptr("existing")}); err == nil {
		t.Fatal("publish duplicate accepted")
	}
	for _, title := range []string{" ", strings.Repeat("Я", 256)} {
		if _, err := svc.PublishAgent(context.Background(), "owner", second.ID, &PublishAgentRequest{Title: &title}); err == nil {
			t.Fatal("publish invalid name accepted")
		}
	}
	if err := dao.DB.Create(&entity.UserTenant{ID: "membership", UserID: "member", TenantID: "owner", Role: "normal", Status: sptr("1")}).Error; err != nil {
		t.Fatal(err)
	}
	if err := dao.DB.Model(&entity.UserCanvas{}).Where("id = ?", first.ID).Update("permission", "team").Error; err != nil {
		t.Fatal(err)
	}
	if err := svc.UpdateAgent(context.Background(), "member", first.ID, map[string]interface{}{"title": "Existing", "dsl": map[string]interface{}{}}); err != nil {
		t.Fatalf("member unchanged autosave failed: %v", err)
	}
	if _, err := svc.PublishAgent(context.Background(), "member", first.ID, &PublishAgentRequest{Title: sptr("Private secret")}); !errors.Is(err, ErrAgentNotOwner) {
		t.Fatalf("member publish rename allowed: %v", err)
	}
	if _, err := svc.PublishAgent(context.Background(), "member", first.ID, &PublishAgentRequest{Title: sptr("Existing")}); err != nil {
		t.Fatalf("member unchanged publish failed: %v", err)
	}
}

func TestAgentNameConflictOnWriteUsesDataError(t *testing.T) {
	setupAgentSessionServiceTest(t)
	if err := dao.DB.Callback().Create().Before("gorm:create").Register("name_conflict", func(tx *gorm.DB) {
		if tx.Statement.Table == "user_canvas" {
			tx.AddError(&pgconn.PgError{Code: "23505", ConstraintName: dao.CanvasNameIndex})
		}
	}); err != nil {
		t.Fatal(err)
	}
	title := "Agent"
	_, code, err := NewAgentService().CreateAgent(context.Background(), &CreateAgentRequest{UserID: "owner", Title: &title, DSL: entity.JSONMap{}})
	if code != common.CodeDataError || err == nil || !strings.Contains(err.Error(), "already exists") {
		t.Fatalf("unexpected conflict envelope: %d %v", code, err)
	}
}

func TestAgentResetPreservesConcurrentRename(t *testing.T) {
	setupAgentSessionServiceTest(t)
	title := "Original"
	svc := NewAgentService()
	canvas, _, err := svc.CreateAgent(context.Background(), &CreateAgentRequest{UserID: "owner", Title: &title, DSL: entity.JSONMap{}})
	if err != nil {
		t.Fatal(err)
	}
	if err := dao.DB.Callback().Update().Before("gorm:update").Register("concurrent_rename", func(tx *gorm.DB) {
		if tx.Statement.Table == "user_canvas" {
			tx.Exec("UPDATE user_canvas SET title = ? WHERE id = ?", "Concurrent rename", canvas.ID)
		}
	}); err != nil {
		t.Fatal(err)
	}
	if _, err := svc.ResetAgent(context.Background(), "owner", canvas.ID); err != nil {
		t.Fatal(err)
	}
	persisted, err := dao.NewUserCanvasDAO().GetByID(canvas.ID)
	if err != nil || persisted.Title == nil || *persisted.Title != "Concurrent rename" {
		t.Fatalf("reset rewrote title: %#v %v", persisted, err)
	}
}

func TestChatNamesCaseConflictsAndCharacterLimit(t *testing.T) {
	db := setupChatRESTUpdateServiceTestDB(t)
	svc := NewChatService()
	first, code, err := svc.Create("user-1", map[string]interface{}{"name": "Existing"})
	if err != nil || code != common.CodeSuccess {
		t.Fatal(err)
	}
	if _, code, err := svc.Create("user-1", map[string]interface{}{"name": "existing"}); err == nil || code != common.CodeDataError {
		t.Fatalf("case collision allowed: %v", err)
	}
	second, code, err := svc.Create("user-1", map[string]interface{}{"name": strings.Repeat("Я", 255)})
	if err != nil || code != common.CodeSuccess {
		t.Fatalf("255 characters rejected: %v", err)
	}
	if _, err := svc.PatchChat("user-1", second["id"].(string), map[string]interface{}{"name": "EXISTING"}); err == nil {
		t.Fatal("patch collision allowed")
	}
	if _, err := svc.UpdateChat("user-1", second["id"].(string), map[string]interface{}{"name": "existing"}); err == nil {
		t.Fatal("put collision allowed")
	}
	if _, err := svc.PatchChat("user-1", first["id"].(string), map[string]interface{}{"name": "EXISTING"}); err != nil {
		t.Fatalf("self case rename rejected: %v", err)
	}
	if err := db.Callback().Create().Before("gorm:create").Register("chat_name_conflict", func(tx *gorm.DB) {
		if tx.Statement.Table == "dialog" {
			tx.AddError(&pgconn.PgError{Code: "23505", ConstraintName: dao.DialogNameIndex})
		}
	}); err != nil {
		t.Fatal(err)
	}
	if _, code, err := svc.Create("user-1", map[string]interface{}{"name": "Available"}); code != common.CodeDataError || err == nil || !strings.Contains(err.Error(), "Duplicated") {
		t.Fatalf("write conflict envelope: %d %v", code, err)
	}
}

func TestDatasetNamesBoundsBMPAndConflictRetry(t *testing.T) {
	db := setupDatasetUpdateTestDB(t)
	pushServiceDB(t, db)
	if err := db.Create(&entity.Tenant{ID: "owner", LLMID: "llm", EmbdID: "embedding", ASRID: "asr", Img2TxtID: "image", RerankID: "rerank", ParserIDs: "naive", Status: sptr("1")}).Error; err != nil {
		t.Fatal(err)
	}
	svc := NewDatasetService()
	for _, name := range []string{"😀", strings.Repeat("Я", 129)} {
		if _, code, err := svc.CreateDataset(&CreateDatasetRequest{Name: name}, "owner"); err == nil || code != common.CodeDataError {
			t.Fatalf("invalid name accepted: %v", err)
		}
	}
	attempts := 0
	if err := db.Callback().Create().Before("gorm:create").Register("dataset_name_conflict_once", func(tx *gorm.DB) {
		if tx.Statement.Table == "knowledgebase" {
			attempts++
			if attempts == 1 {
				tx.AddError(&pgconn.PgError{Code: "23505", ConstraintName: dao.KnowledgebaseNameIndex})
			}
		}
	}); err != nil {
		t.Fatal(err)
	}
	name := strings.Repeat("Я", 128)
	result, code, err := svc.CreateDataset(&CreateDatasetRequest{Name: name, Ext: map[string]interface{}{"tenant_id": "victim", "id": "replacement", "created_by": "victim", "status": "0"}}, "owner")
	if err != nil || code != common.CodeSuccess {
		t.Fatalf("create retry failed: %v", err)
	}
	actual := result["name"].(string)
	if attempts != 2 || utf8.RuneCountInString(actual) != 128 || !strings.HasSuffix(actual, "(1)") {
		t.Fatalf("invalid retry result attempts=%d name=%q", attempts, actual)
	}
	id := result["id"].(string)
	for _, name := range []string{"😀", strings.Repeat("Я", 129)} {
		if _, code, err := svc.UpdateDataset(id, "owner", UpdateDatasetRequest{Name: &name}); err == nil || code != common.CodeDataError {
			t.Fatalf("invalid rename accepted: %v", err)
		}
	}
	for _, invalid := range []interface{}{" ", "😀", strings.Repeat("Я", 129), 17} {
		if _, code, err := svc.UpdateDataset(id, "owner", UpdateDatasetRequest{Ext: map[string]interface{}{"name": invalid}}); err == nil || code != common.CodeDataError {
			t.Fatalf("invalid ext name accepted: %v", err)
		}
	}
	validName := "Renamed"
	if _, code, err := svc.UpdateDataset(id, "owner", UpdateDatasetRequest{Name: &validName, Ext: map[string]interface{}{"tenant_id": "victim", "id": "replacement", "created_by": "victim", "status": "0"}}); err != nil || code != common.CodeSuccess {
		t.Fatalf("rename with ignored readonly keys failed: %v", err)
	}
	persisted, err := dao.NewKnowledgebaseDAO().GetByID(id)
	if err != nil || persisted.TenantID != "owner" || persisted.CreatedBy != "owner" || persisted.Status == nil || *persisted.Status != "1" {
		t.Fatalf("readonly scope changed: %#v %v", persisted, err)
	}

	insertDatasetUpdateKB(t, "private-kb", "owner", "Private")
	newName := "PRIVATE"
	if _, code, err := svc.UpdateDataset(id, "owner", UpdateDatasetRequest{Name: &newName}); err == nil || code != common.CodeDataError {
		t.Fatalf("case rename collision accepted: %v", err)
	}
}
