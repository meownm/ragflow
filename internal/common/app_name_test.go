package common

import (
	"errors"
	"strings"
	"testing"
	"unicode/utf8"
)

func TestNormalizeResourceNameCharacters(t *testing.T) {
	for _, tc := range []struct {
		name       string
		limit      int
		bmp, valid bool
	}{
		{"  Привет  ", 6, true, true},
		{strings.Repeat("Я", 128), 128, true, true},
		{strings.Repeat("Я", 129), 128, true, false},
		{strings.Repeat("😀", 255), 255, false, true},
		{"😀", 128, true, false},
		{"  ", 128, true, false},
		{"a\x00b", 128, true, false},
	} {
		actual, err := NormalizeResourceName(tc.name, tc.limit, tc.bmp)
		if (err == nil) != tc.valid || (err == nil && actual != strings.TrimSpace(tc.name)) {
			t.Fatalf("NormalizeResourceName(%q): value=%q err=%v", tc.name, actual, err)
		}
	}
}

func TestDuplicateNameWithLimitPreservesBoundsAndExtension(t *testing.T) {
	for _, original := range []string{strings.Repeat("Я", 128), strings.Repeat("Я", 120) + "(8).txt", "dataset.txt"} {
		seen := map[string]bool{original: true}
		first := ""
		for i := 0; i < 12; i++ {
			name, err := DuplicateNameWithLimit(func(name, owner string) (bool, error) {
				if owner != "owner" {
					t.Fatal("wrong owner scope")
				}
				return seen[name], nil
			}, original, "owner", 128)
			if err != nil || utf8.RuneCountInString(name) > 128 || seen[name] {
				t.Fatalf("name=%q err=%v", name, err)
			}
			if strings.HasSuffix(original, ".txt") && !strings.HasSuffix(name, ".txt") {
				t.Fatalf("extension lost: %q", name)
			}
			if i == 0 {
				first = name
			}
			seen[name] = true
		}
		if strings.Contains(original, "(8)") && !strings.Contains(first, "(9)") {
			t.Fatalf("counter not continued: %q", first)
		}
	}
}

func TestDuplicateNameWithLimitPropagatesStorageError(t *testing.T) {
	want := errors.New("database unavailable")
	_, err := DuplicateNameWithLimit(func(string, string) (bool, error) { return false, want }, "dataset", "owner", 128)
	if !errors.Is(err, want) {
		t.Fatalf("lost storage error: %v", err)
	}
	calls := 0
	_, err = DuplicateNameWithLimit(func(string, string) (bool, error) { calls++; return true, nil }, "dataset", "owner", 128)
	if err == nil || calls != 1000 {
		t.Fatalf("retry bound calls=%d err=%v", calls, err)
	}
}
