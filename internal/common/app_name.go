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

package common

import (
	"fmt"
	"path"
	"regexp"
	"strings"
	"unicode/utf8"
)

// splitNameCounter splits a filename into base name and counter
// Handles names in format "filename(123)" pattern
//
// Parameters:
//   - filename: The filename to split
//
// Returns:
//   - string: The base name without counter
//   - *int: The counter value, or nil if no counter exists
//
// Example:
//
//	splitNameCounter("test(5)") returns ("test", 5)
//	splitNameCounter("test") returns ("test", nil)
func splitNameCounter(filename string) (string, *int) {
	re := regexp.MustCompile(`^(.+)\((\d+)\)$`)
	matches := re.FindStringSubmatch(filename)
	if len(matches) >= 3 {
		counter := -1
		fmt.Sscanf(matches[2], "%d", &counter)
		stem := strings.TrimRight(matches[1], " ")
		return stem, &counter
	}
	return filename, nil
}

// DuplicateName generates a unique name by appending a counter if the name already exists
// It tries up to 1000 times to generate a unique name
//
// Parameters:
//   - queryFunc: Function to check if a name already exists (returns true if exists)
//   - name: The original name
//   - tenantID: The tenant ID for name uniqueness check
//
// Returns:
//   - string: A unique name (either original or with counter appended)
//
// Example:
//
//	DuplicateName(func(name string, tid string) bool { return false }, "test", "tenant1") returns "test"
//	DuplicateName(func(name string, tid string) bool { return true }, "test", "tenant1") returns "test(1)"
func DuplicateName(queryFunc func(name string, tenantID string) bool, name string, tenantID string) (string, error) {
	const maxRetries = 1000

	originalName := name
	currentName := name
	retries := 0

	for retries < maxRetries {
		if !queryFunc(currentName, tenantID) {
			return currentName, nil
		}

		stem, counter := splitNameCounter(currentName)
		ext := path.Ext(stem)
		stemBase := strings.TrimSuffix(stem, ext)

		newCounter := 1
		if counter != nil {
			newCounter = *counter + 1
		}

		currentName = fmt.Sprintf("%s(%d)%s", stemBase, newCounter, ext)
		retries++

		if err := ValidateName(currentName); err != nil {
			return "", err
		}
	}

	return "", fmt.Errorf("failed to generate unique name after %d attempts, conflict name: %s", maxRetries, originalName)
}

const AppNameLimit = 256

// NormalizeResourceName applies the character limit used by chats, agents and datasets.
func NormalizeResourceName(name string, maxChars int, bmpOnly bool) (string, error) {
	name = strings.TrimSpace(name)
	if name == "" {
		return "", fmt.Errorf("name cannot be empty")
	}
	if !utf8.ValidString(name) || strings.ContainsRune(name, 0) {
		return "", fmt.Errorf("name contains invalid characters")
	}
	if utf8.RuneCountInString(name) > maxChars {
		return "", fmt.Errorf("name must have at most %d characters", maxChars)
	}
	if bmpOnly {
		for _, char := range name {
			if char > 0xffff || (char >= 0xd800 && char <= 0xdfff) {
				return "", fmt.Errorf("name must contain only BMP characters")
			}
		}
	}
	return name, nil
}

// DuplicateNameWithLimit finds a bounded character-length name and propagates
// storage errors. Existing file extensions and numeric suffixes are preserved.
func DuplicateNameWithLimit(query func(string, string) (bool, error), name, tenantID string, maxChars int) (string, error) {
	var err error
	name, err = NormalizeResourceName(name, maxChars, false)
	if err != nil {
		return "", err
	}
	ext := path.Ext(name)
	stem, counter := splitNameCounter(strings.TrimSuffix(name, ext))
	next := 1
	if counter != nil {
		next = *counter + 1
	}
	candidate := name
	for attempt := 0; attempt < 1000; attempt++ {
		exists, err := query(candidate, tenantID)
		if err != nil {
			return "", err
		}
		if !exists {
			return candidate, nil
		}
		suffix := fmt.Sprintf("(%d)", next+attempt)
		budget := maxChars - utf8.RuneCountInString(suffix)
		if budget < 1 {
			return "", fmt.Errorf("name limit cannot accommodate a suffix")
		}
		extRunes := []rune(ext)
		if len(extRunes) >= budget {
			extRunes = extRunes[:budget-1]
		}
		stemRunes := []rune(stem)
		if len(stemRunes) > budget-len(extRunes) {
			stemRunes = stemRunes[:budget-len(extRunes)]
		}
		candidate = string(stemRunes) + suffix + string(extRunes)
	}
	return "", fmt.Errorf("failed to generate a unique name after 1000 attempts")
}

func ValidateName(name string) error {
	// Validate name is not empty after trimming
	trimmedName := strings.TrimSpace(name)
	if trimmedName == "" {
		return fmt.Errorf("name can't be empty")
	}

	// Validate name length in bytes (not characters) - same as Python len(search_name.encode("utf-8"))
	if len([]byte(name)) > AppNameLimit {
		return fmt.Errorf("name length is %d which is large than %d", len([]byte(name)), AppNameLimit)
	}

	return nil
}
