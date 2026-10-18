package dao

import (
	"fmt"
	"gorm.io/driver/postgres"
	"gorm.io/gorm"
	"net/url"
	"os"
	"ragflow/internal/server"
	"testing"
	"time"
)

func TestPostgresDSNEscapesCredentials(t *testing.T) {
	config := server.DatabaseConfig{Host: "::1", Port: 5432, Database: "data with spaces", Username: "user@name", Password: "p:a/ss?word#", SSLMode: "verify-full"}
	parsed, err := url.Parse(postgresDSN(config))
	if err != nil {
		t.Fatal(err)
	}
	password, _ := parsed.User.Password()
	if parsed.Scheme != "postgresql" || parsed.Host != "[::1]:5432" || parsed.User.Username() != config.Username || password != config.Password || parsed.Path != "/"+config.Database || parsed.Query().Get("sslmode") != "verify-full" {
		t.Fatalf("PostgreSQL connection fields did not round-trip")
	}
}

func TestPostgresMigrationsPreserveLegacyRows(t *testing.T) {
	dsn := os.Getenv("RAGFLOW_GO_TEST_POSTGRES_DSN")
	if dsn == "" {
		t.Skip("requires disposable PostgreSQL DSN")
	}
	if os.Getenv("RAGFLOW_LINKED_TEST_DISPOSABLE") != "1" {
		t.Fatal("explicit disposable database flag required")
	}
	connection, err := url.Parse(dsn)
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
	defer adminSQL.Close()
	schema := fmt.Sprintf("linked_go_%d", time.Now().UnixNano())
	if err := admin.Exec("CREATE SCHEMA " + schema).Error; err != nil {
		t.Fatal(err)
	}
	defer admin.Exec("DROP SCHEMA " + schema + " CASCADE")
	query := connection.Query()
	query.Set("search_path", schema)
	connection.RawQuery = query.Encode()
	db, err := gorm.Open(postgres.Open(connection.String()), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	sqlDB, err := db.DB()
	if err != nil {
		t.Fatal(err)
	}
	defer sqlDB.Close()
	statements := []string{
		`CREATE TABLE tenant_llm (tenant_id text, llm_factory text, llm_name text, api_key text, PRIMARY KEY (tenant_id,llm_factory,llm_name))`,
		`INSERT INTO tenant_llm VALUES ('tenant','provider','model','fixture-value')`,
		`CREATE TABLE task (process_duation real)`,
		`INSERT INTO task VALUES (12.5)`,
		`CREATE TABLE "user" (id text, email text)`,
		`INSERT INTO "user" VALUES ('user1','fixture@example.test')`,
	}
	for _, statement := range statements {
		if err := db.Exec(statement).Error; err != nil {
			t.Fatal(err)
		}
	}
	for i := 0; i < 2; i++ {
		if err := RunMigrations(db); err != nil {
			t.Fatal(err)
		}
	}
	var value string
	if err := db.Raw(`SELECT api_key FROM tenant_llm WHERE id IS NOT NULL`).Scan(&value).Error; err != nil || value != "fixture-value" {
		t.Fatalf("legacy row changed: %v", err)
	}
	var duration float64
	if err := db.Raw(`SELECT process_duration FROM task`).Scan(&duration).Error; err != nil || duration != 12.5 {
		t.Fatalf("renamed column lost data: %v", err)
	}
	if !db.Migrator().HasTable("skill_spaces") || !db.Migrator().HasTable("skill_search_configs") {
		t.Fatal("missing skill tables")
	}
}
