package dao

import "testing"

func TestHydrateCanvasGraphForms(t *testing.T) {
	data := map[string]any{
		"dsl": map[string]any{
			"components": map[string]any{
				"Agent:OCR": map[string]any{
					"obj": map[string]any{
						"params": map[string]any{"llm_id": "vision@local", "outputs": map[string]any{}},
					},
				},
			},
			"graph": map[string]any{
				"nodes": []any{
					map[string]any{
						"id":   "Agent:OCR",
						"data": map[string]any{"form": map[string]any{"llm_id": "stale"}},
					},
				},
			},
		},
	}

	hydrateCanvasGraphForms(data)

	dsl := data["dsl"].(map[string]any)
	nodes := dsl["graph"].(map[string]any)["nodes"].([]any)
	form := nodes[0].(map[string]any)["data"].(map[string]any)["form"].(map[string]any)
	if got := form["llm_id"]; got != "vision@local" {
		t.Fatalf("llm_id = %v, want vision@local", got)
	}
	form["llm_id"] = "changed"
	params := dsl["components"].(map[string]any)["Agent:OCR"].(map[string]any)["obj"].(map[string]any)["params"].(map[string]any)
	if got := params["llm_id"]; got != "vision@local" {
		t.Fatalf("component params mutated through graph form: %v", got)
	}
}
