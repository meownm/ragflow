#!/usr/bin/env bash
# Execute native code in the qualified image without a source or library overlay.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "${repo_root}/deployment/runner/artifact-shell.sh"
artifact_managed_entry native-image-qa "$@"
image="${RAGFLOW_IMAGE:?Set the qualified image reference including @sha256 digest}"
source_sha="${SOURCE_SHA:?}"
parser_binary="${1:?Usage: test-native-image.sh parser.test output-directory}"
output="${2:?Usage: test-native-image.sh parser.test output-directory}"
[[ "$image" =~ @sha256:[0-9a-f]{64}$ ]] || { echo 'Native qualification requires an image digest' >&2; exit 1; }
[[ "$source_sha" =~ ^[0-9a-f]{40}$ ]] || { echo 'Invalid source SHA' >&2; exit 1; }
test -f "$parser_binary"
test ! -L "$parser_binary"
mkdir -p "$output"
output="$(cd "$output" && pwd)"
fixture="$(mktemp -d "${RUNNER_TEMP:-/tmp}/ragflow-native.XXXXXXXX")"
container=""
cleanup() {
  status=$?
  trap - EXIT
  if [[ -n "$container" ]]; then
    docker rm -f "$container" >/dev/null || status=1
  fi
  if [[ "$status" != 0 ]]; then
    python3 - "$output/native-image.json" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
report = json.loads(path.read_text()) if path.exists() else {}
report.update(status="FAIL", reason="Native image qualification or owned-resource cleanup failed")
path.write_text(json.dumps(report, indent=2) + "\n")
PY
  fi
  python3 -B "$artifact_driver" record --category evidence --evidence "$fixture" || status=1
  python3 -B "$artifact_driver" record --category evidence --evidence "$output" || status=1
  if [[ "$status" == 0 ]]; then
    rm -rf -- "$fixture"
  else
    echo "Native QA materials retained: $fixture" >&2
  fi
  exit "$status"
}
trap cleanup EXIT

mkdir -p "$fixture/internal/parser/parser" "$fixture/test/benchmark/test_docs"
cp "$parser_binary" "$fixture/parser.test"
chmod 755 "$fixture/parser.test"
cp "$repo_root/test/benchmark/test_docs/Doc1.pdf" "$fixture/test/benchmark/test_docs/Doc1.pdf"
python3 - "$fixture" "$output/native-image.json" "$image" "$source_sha" <<'PY'
import hashlib, json, os, pathlib, sys, zipfile
root, receipt, image, sha = sys.argv[1:]
root = pathlib.Path(root)
entries = {
    "[Content_Types].xml": '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
    "_rels/.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
    "word/document.xml": '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>RAGFlow native DOCX qualification marker</w:t></w:r></w:p></w:body></w:document>',
}
with zipfile.ZipFile(root / "native.docx", "w", zipfile.ZIP_DEFLATED) as archive:
    for name, body in sorted(entries.items()):
        archive.writestr(zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0)), body)
files = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
         for path in sorted(root.rglob("*")) if path.is_file()}
report = dict(schema=1, purpose="native-final-image", status="INCOMPLETE", source_sha=sha,
              image_digest=image, repository=os.environ.get("GITHUB_REPOSITORY"),
              run_id=os.environ.get("GITHUB_RUN_ID"), attempt=os.environ.get("GITHUB_RUN_ATTEMPT"),
              fixtures=files, instrumentation="separately compiled repository parser test binary",
              product_commands=["image CLI PDF plain_text", "image CLI DOCX office_oxide"],
              pdf_layout="mock analyzer; real PDF extraction and rendering",
              real_ocr_models="not qualified by this native check")
pathlib.Path(receipt).write_text(json.dumps(report, indent=2) + "\n")
PY

# The normal engine acceptance container keeps the image entrypoint. This
# separate native probe executes the image binaries with no network or writes
# to the image filesystem; it cannot substitute a locally rebuilt product CLI.
container=$(docker create --read-only --network none --cap-drop ALL \
  --security-opt no-new-privileges --tmpfs /tmp:rw,nosuid,nodev \
  --env HOME=/tmp --env DEEPDOC_URL= --env OSSDEEPDOC_URL= \
  --label "ragflow.qualification=${GITHUB_REPOSITORY:-local}/${GITHUB_RUN_ID:-local}/${GITHUB_RUN_ATTEMPT:-1}/native" \
  --mount "type=bind,src=${fixture},dst=/qualification,readonly" \
  --workdir /qualification/internal/parser/parser --entrypoint /bin/bash "$image" \
  -c 'set -euo pipefail
      /qualification/parser.test -test.v -test.run "^(TestPDFParser_ParseWithResult_CGOFixture|TestPDFParser_ParseWithResult_CGOFixtureMarkdown|TestPDFParser_ParseWithResult_CGOFixturePlainText|TestPDFParser_ParseWithResult_CGOInvalidPDF|TestPDFParser_ParseWithResult_CGOEmpty|TestDOCXParser_ParseWithResult_CGOMinimalDocument)$"
      printf "\nNATIVE_CLI_PDF_BEGIN\n"
      /ragflow/bin/ragflow-cli "parse file '\''/qualification/test/benchmark/test_docs/Doc1.pdf'\'' doc_parse '\''plain_text'\'';"
      printf "\nNATIVE_CLI_DOCX_BEGIN\n"
      /ragflow/bin/ragflow-cli "parse file '\''/qualification/native.docx'\'';"
      printf "\nNATIVE_IMAGE_CLI_SHA256 "
      sha256sum /ragflow/bin/ragflow-cli
      printf "\nNATIVE_SOURCE_SHA "
      cat /ragflow/SOURCE_REVISION')
docker start -a "$container" 2>&1 | tee "$output/native-image.log"
test "$(docker inspect "$container" --format '{{.State.ExitCode}}')" = 0
test "$(docker inspect "$container" --format '{{.Image}}')" = "$(docker image inspect "$image" --format '{{.Id}}')"
python3 - "$output/native-image.json" "$output/native-image.log" <<'PY'
import hashlib, json, pathlib, re, sys
receipt, log = map(pathlib.Path, sys.argv[1:])
report = json.loads(receipt.read_text())
text = log.read_text()
expected = {
    "TestPDFParser_ParseWithResult_CGOFixture", "TestPDFParser_ParseWithResult_CGOFixtureMarkdown",
    "TestPDFParser_ParseWithResult_CGOFixturePlainText", "TestPDFParser_ParseWithResult_CGOInvalidPDF",
    "TestPDFParser_ParseWithResult_CGOEmpty", "TestDOCXParser_ParseWithResult_CGOMinimalDocument",
}
passed_cases = re.findall(r"^--- PASS: (\S+) \(", text, re.M)
passed = set(passed_cases)
if passed != expected or len(passed_cases) != len(expected) or re.search(r"^--- (?:SKIP|FAIL):", text, re.M):
    raise SystemExit(f"Native cases incomplete: missing={sorted(expected-passed)}, extra={sorted(passed-expected)}")
pdf = text.split("NATIVE_CLI_PDF_BEGIN\n", 1)[1].split("NATIVE_CLI_DOCX_BEGIN\n", 1)[0]
docx = text.split("NATIVE_CLI_DOCX_BEGIN\n", 1)[1].split("NATIVE_IMAGE_CLI_SHA256", 1)[0]
if "Purpose of RAGFlow" not in pdf or "RAGFlow native DOCX qualification marker" not in docx:
    raise SystemExit("Image CLI did not return expected PDF/DOCX content")
if not re.search(r"^NATIVE_SOURCE_SHA " + re.escape(report["source_sha"]) + r"\s*$", text, re.M):
    raise SystemExit("Image source identity differs from qualification source")
cli_hash = re.search(r"NATIVE_IMAGE_CLI_SHA256 ([a-f0-9]{64})\s", text)
if not cli_hash:
    raise SystemExit("Image CLI identity missing")
report.update(status="PASS", cases=sorted(passed), image_cli_sha256=cli_hash[1],
              log_sha256=hashlib.sha256(log.read_bytes()).hexdigest())
receipt.write_text(json.dumps(report, indent=2) + "\n")
PY
