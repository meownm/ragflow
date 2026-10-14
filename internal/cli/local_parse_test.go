package cli

import (
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestAPIParseLocalFileCommand_PrintsExtractedText(t *testing.T) {
	filename := filepath.Join(t.TempDir(), "content.txt")
	if err := os.WriteFile(filename, []byte("native qualification marker"), 0600); err != nil {
		t.Fatal(err)
	}
	client := &CLI{Config: &CommandLineConfig{CLIMode: APIMode}}
	cmd := NewCommand("user_parse_local_file_command")
	cmd.Params["filename"] = filename
	output, err := os.CreateTemp(t.TempDir(), "stdout")
	if err != nil {
		t.Fatal(err)
	}
	defer output.Close()
	stdout := os.Stdout
	os.Stdout = output
	t.Cleanup(func() { os.Stdout = stdout })
	response, err := client.APIParseLocalFileCommand(cmd)
	os.Stdout = stdout
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	if response.(*SimpleResponse).Code != 0 {
		t.Fatalf("response = %#v", response)
	}
	if _, err := output.Seek(0, 0); err != nil {
		t.Fatal(err)
	}
	content, err := io.ReadAll(output)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(content), "native qualification marker") {
		t.Fatalf("extracted content missing from output: %q", content)
	}
}

func TestAPIParseLocalFileCommand_AppliesPDFParseMethod(t *testing.T) {
	filename := filepath.Join(t.TempDir(), "invalid.pdf")
	if err := os.WriteFile(filename, []byte("not a PDF"), 0600); err != nil {
		t.Fatal(err)
	}
	client := &CLI{Config: &CommandLineConfig{CLIMode: APIMode}}
	cmd := NewCommand("user_parse_local_file_command")
	cmd.Params["filename"] = filename
	cmd.Params["doc_parse_model"] = "unavailable-method"
	_, err := client.APIParseLocalFileCommand(cmd)
	if err == nil || !strings.Contains(err.Error(), "unsupported PDF parse_method") {
		t.Fatalf("parse method was ignored: %v", err)
	}
}

func TestAPIParseLocalFileCommand_RejectsAdminMode(t *testing.T) {
	client := &CLI{Config: &CommandLineConfig{CLIMode: AdminMode}}
	_, err := client.APIParseLocalFileCommand(NewCommand("user_parse_local_file_command"))
	if err == nil || !strings.Contains(err.Error(), "USER mode") {
		t.Fatalf("error = %v", err)
	}
}

func TestAPIParseLocalFileCommand_ReportsMissingLocalFile(t *testing.T) {
	client := &CLI{Config: &CommandLineConfig{CLIMode: APIMode}}
	cmd := NewCommand("user_parse_local_file_command")
	cmd.Params["filename"] = filepath.Join(t.TempDir(), "missing.txt")
	_, err := client.APIParseLocalFileCommand(cmd)
	if err == nil || !strings.Contains(err.Error(), "failed to read local file") {
		t.Fatalf("error = %v", err)
	}
}
