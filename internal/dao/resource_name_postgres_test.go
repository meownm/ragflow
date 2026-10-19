package dao

import (
	"fmt"
	"net/url"
	"os"
	"strings"
	"sync"
	"testing"
	"time"

	"gorm.io/driver/postgres"
	"gorm.io/gorm"
)

func setupResourceNamePostgres(t *testing.T) *gorm.DB {
	t.Helper()
	dsn := os.Getenv("RAGFLOW_GO_TEST_POSTGRES_DSN")
	if dsn == "" {
		t.Skip("requires disposable PostgreSQL DSN")
	}
	if os.Getenv("RAGFLOW_LINKED_TEST_DISPOSABLE") != "1" {
		t.Fatal("explicit disposable database flag required")
	}
	parsed, err := url.Parse(dsn)
	if err != nil {
		t.Fatal(err)
	}
	admin, err := gorm.Open(postgres.Open(dsn), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	adminSQL, err := admin.DB()
	if err != nil {
		t.Fatal(err)
	}
	schema := fmt.Sprintf("resource_names_%d", time.Now().UnixNano())
	if err := admin.Exec("CREATE SCHEMA " + schema).Error; err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { admin.Exec("DROP SCHEMA " + schema + " CASCADE"); adminSQL.Close() })
	params := parsed.Query()
	params.Set("search_path", schema)
	parsed.RawQuery = params.Encode()
	db, err := gorm.Open(postgres.Open(parsed.String()), &gorm.Config{TranslateError: true})
	if err != nil {
		t.Fatal(err)
	}
	sqlDB, err := db.DB()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { sqlDB.Close() })
	for _, sql := range []string{
		`CREATE TABLE dialog (id text PRIMARY KEY, tenant_id text NOT NULL, name text NOT NULL, status text NOT NULL)`,
		`CREATE TABLE knowledgebase (id text PRIMARY KEY, tenant_id text NOT NULL, name text NOT NULL, status text NOT NULL)`,
		`CREATE TABLE user_canvas (id text PRIMARY KEY, user_id text NOT NULL, canvas_category text NOT NULL, title text NOT NULL)`,
	} {
		if err := db.Exec(sql).Error; err != nil {
			t.Fatal(err)
		}
	}
	return db
}

func TestResourceNamePostgresMigrationAndConcurrentWrites(t *testing.T) {
	db := setupResourceNamePostgres(t)
	for i := 0; i < 2; i++ {
		if err := migrateResourceNames(db); err != nil {
			t.Fatal(err)
		}
	}
	for _, tc := range []struct{ index, sql string }{
		{DialogNameIndex, `INSERT INTO dialog VALUES (?, ?, ?, '1')`},
		{KnowledgebaseNameIndex, `INSERT INTO knowledgebase VALUES (?, ?, ?, '1')`},
		{CanvasNameIndex, `INSERT INTO user_canvas VALUES (?, ?, 'agent_canvas', ?)`},
	} {
		start := make(chan struct{})
		errors := make(chan error, 2)
		var wg sync.WaitGroup
		for i, name := range []string{" Название ", "название"} {
			wg.Add(1)
			go func(i int, name string) {
				defer wg.Done()
				<-start
				errors <- resourceNameWrite(db).Exec(tc.sql, fmt.Sprint(i), "owner", name).Error
			}(i, name)
		}
		close(start)
		wg.Wait()
		close(errors)
		ok, conflicts := 0, 0
		for err := range errors {
			if err == nil {
				ok++
			} else if IsResourceNameConflict(err, tc.index) {
				conflicts++
			} else {
				t.Fatalf("unexpected error: %v", err)
			}
		}
		if ok != 1 || conflicts != 1 {
			t.Fatalf("index %s writes=%d conflicts=%d", tc.index, ok, conflicts)
		}
		if err := resourceNameWrite(db).Exec(tc.sql, "peer", "peer", "название").Error; err != nil {
			t.Fatalf("owner scopes merged: %v", err)
		}
	}
	for _, sql := range []string{
		`INSERT INTO dialog VALUES ('deleted','owner','название','0')`,
		`INSERT INTO knowledgebase VALUES ('deleted','owner','название','0')`,
		`INSERT INTO user_canvas VALUES ('dataflow','owner','dataflow_canvas','название')`,
	} {
		if err := db.Exec(sql).Error; err != nil {
			t.Fatal(err)
		}
	}
	if !db.Config.TranslateError {
		t.Fatal("global error translation changed")
	}
}

func TestResourceNamePostgresMigrationFailsClosedWithoutPrivateDetails(t *testing.T) {
	db := setupResourceNamePostgres(t)
	if err := db.Exec(`INSERT INTO knowledgebase VALUES ('a','private-owner','Private secret','1'),('b','private-owner',' private SECRET ','1')`).Error; err != nil {
		t.Fatal(err)
	}
	err := migrateResourceNames(db)
	if err == nil || strings.Contains(err.Error(), "Private") || strings.Contains(err.Error(), "private-owner") {
		t.Fatalf("migration did not fail safely: %v", err)
	}
	var count int64
	if err := db.Table("knowledgebase").Count(&count).Error; err != nil || count != 2 {
		t.Fatalf("rows changed: count=%d err=%v", count, err)
	}
	if err := db.Raw(`SELECT COUNT(*) FROM pg_indexes WHERE schemaname=current_schema() AND indexname IN (?, ?, ?)`, DialogNameIndex, KnowledgebaseNameIndex, CanvasNameIndex).Scan(&count).Error; err != nil || count != 0 {
		t.Fatalf("partial migration committed: count=%d err=%v", count, err)
	}
}
