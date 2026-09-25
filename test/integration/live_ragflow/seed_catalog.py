"""Ensure the disposable QA database has the canonical model factory catalog."""

import json
import os
import psycopg2

assert os.environ["POSTGRES_HOST"] == "postgres"
with open("/ragflow/conf/llm_factories.json") as source:
    catalog = json.load(source)["factory_llm_infos"]
with psycopg2.connect(host="postgres", dbname=os.environ["POSTGRES_DBNAME"], user=os.environ["POSTGRES_USER"], password=os.environ["POSTGRES_PASSWORD"]) as connection:
    with connection.cursor() as cursor:
        cursor.execute("SELECT name FROM llm_factories")
        existing = {row[0] for row in cursor.fetchall()}
        missing = [row for row in catalog if row["name"] not in existing]
        cursor.executemany(
            "INSERT INTO llm_factories(name,logo,tags,rank,status) VALUES (%s,%s,%s,%s,%s)",
            [(row["name"], row.get("logo", ""), row["tags"], int(row.get("rank", 0)), row.get("status", "1")) for row in missing],
        )
print(f"Seeded {len(missing)} missing canonical QA factory records")
