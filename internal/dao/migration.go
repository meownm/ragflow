//
//  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
//
//  Licensed under the Apache License, Version 2.0 (the "License");
//  you may not use this file except in compliance with the License.
//  You may obtain a copy of the License at
//
//      http://www.apache.org/licenses/LICENSE-2.0
//
//  Unless required by applicable law or agreed to in writing, software
//  distributed under the License is distributed on an "AS IS" BASIS,
//  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
//  See the License for the specific language governing permissions and
//  limitations under the License.
//

package dao

import (
	"fmt"
	"ragflow/internal/common"
	"ragflow/internal/entity"

	"go.uber.org/zap"
	"gorm.io/gorm"
)

// RunMigrations applies the PostgreSQL schema changes shared with the API.
func RunMigrations(db *gorm.DB) error {
	if err := migrateTenantLLMPrimaryKey(db); err != nil {
		return err
	}
	for _, table := range []string{"task", "document"} {
		if err := renameColumnIfExists(db, table, "process_duation", "process_duration"); err != nil {
			return err
		}
	}
	if err := migrateAddUniqueEmail(db); err != nil {
		return err
	}
	if err := modifyColumnTypes(db); err != nil {
		return err
	}
	if err := migrateSkillSearchTables(db); err != nil {
		return err
	}
	return migrateSkillSpaceTables(db)
}

func migrateTenantLLMPrimaryKey(db *gorm.DB) error {
	if !db.Migrator().HasTable("tenant_llm") || db.Migrator().HasColumn("tenant_llm", "id") {
		return nil
	}
	return db.Transaction(func(tx *gorm.DB) error {
		statements := []string{
			`ALTER TABLE tenant_llm ADD COLUMN id BIGSERIAL`,
			`DO $$ DECLARE key_name text; BEGIN
                SELECT conname INTO key_name FROM pg_constraint
                WHERE conrelid = 'tenant_llm'::regclass AND contype = 'p';
                IF key_name IS NOT NULL THEN
                    EXECUTE format('ALTER TABLE tenant_llm DROP CONSTRAINT %I', key_name);
                END IF;
            END $$`,
			`ALTER TABLE tenant_llm ADD PRIMARY KEY (id)`,
			`CREATE UNIQUE INDEX IF NOT EXISTS idx_tenant_llm_unique ON tenant_llm (tenant_id, llm_factory, llm_name)`,
		}
		for _, statement := range statements {
			if err := tx.Exec(statement).Error; err != nil {
				return err
			}
		}
		return nil
	})
}

func migrateAddUniqueEmail(db *gorm.DB) error {
	if !db.Migrator().HasTable("user") {
		return nil
	}
	var duplicateCount int64
	if err := db.Raw(`SELECT COUNT(*) FROM (SELECT email FROM "user" GROUP BY email HAVING COUNT(*) > 1) AS duplicates`).Scan(&duplicateCount).Error; err != nil {
		return err
	}
	if duplicateCount > 0 {
		common.Warn("Duplicate emails prevent creating a unique index", zap.Int64("count", duplicateCount))
		return nil
	}
	return db.Exec(`CREATE UNIQUE INDEX IF NOT EXISTS idx_user_email_unique ON "user" (email)`).Error
}

func modifyColumnTypes(db *gorm.DB) error {
	changes := []struct{ table, column, definition string }{
		{"dialog", "top_k", "TYPE BIGINT"},
		{"tenant_llm", "api_key", "TYPE TEXT"},
		{"api_token", "dialog_id", "TYPE VARCHAR(32)"},
		{"canvas_template", "title", "TYPE TEXT"},
		{"canvas_template", "description", "TYPE TEXT"},
		{"system_settings", "value", "TYPE TEXT"},
		{"knowledgebase", "raptor_task_finish_at", "TYPE TIMESTAMP USING raptor_task_finish_at::timestamp"},
		{"knowledgebase", "mindmap_task_finish_at", "TYPE TIMESTAMP USING mindmap_task_finish_at::timestamp"},
	}
	for _, change := range changes {
		if db.Migrator().HasTable(change.table) && db.Migrator().HasColumn(change.table, change.column) {
			if err := db.Exec(fmt.Sprintf(`ALTER TABLE %q ALTER COLUMN %q %s`, change.table, change.column, change.definition)).Error; err != nil {
				return err
			}
		}
	}
	if db.Migrator().HasTable("dialog") && db.Migrator().HasColumn("dialog", "top_k") {
		if err := db.Exec(`ALTER TABLE dialog ALTER COLUMN top_k SET DEFAULT 1024`).Error; err != nil {
			return err
		}
	}
	return nil
}

func renameColumnIfExists(db *gorm.DB, tableName, oldName, newName string) error {
	if !db.Migrator().HasTable(tableName) || !db.Migrator().HasColumn(tableName, oldName) || db.Migrator().HasColumn(tableName, newName) {
		return nil
	}
	return db.Exec(fmt.Sprintf(`ALTER TABLE %q RENAME COLUMN %q TO %q`, tableName, oldName, newName)).Error
}

func migrateSkillSearchTables(db *gorm.DB) error {
	if err := db.AutoMigrate(&entity.SkillSearchConfig{}); err != nil {
		return err
	}
	if err := db.Exec(`DROP INDEX IF EXISTS idx_tenant_embd`).Error; err != nil {
		return err
	}
	return db.Exec(`CREATE UNIQUE INDEX IF NOT EXISTS idx_tenant_space_embd ON skill_search_configs (tenant_id, space_id, embd_id)`).Error
}

func migrateSkillSpaceTables(db *gorm.DB) error {
	if err := db.AutoMigrate(&entity.SkillSpace{}); err != nil {
		return err
	}
	if err := db.Exec(`DROP INDEX IF EXISTS idx_tenant_name`).Error; err != nil {
		return err
	}
	return db.Exec(`CREATE UNIQUE INDEX IF NOT EXISTS idx_tenant_name_status ON skill_spaces (tenant_id, name, status)`).Error
}
