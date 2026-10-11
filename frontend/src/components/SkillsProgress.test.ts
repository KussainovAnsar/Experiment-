import { describe, expect, it } from "vitest";
import { lessonHistory } from "./SkillsProgress";

describe("lessonHistory (parent view of skill evidence)", () => {
  it("collapses raw evidence to one line per lesson, newest first, summary status wins", () => {
    const history = lessonHistory([
      { lesson_id: "a", lesson_at: "2026-10-01T10:00:00Z", timestamp_sec: 94, note: "Ошибка: …", status: "struggling", kind: "error" },
      { lesson_id: "a", lesson_at: "2026-10-01T10:00:00Z", timestamp_sec: 94, note: "Итог урока", status: "developing", kind: "summary" },
      { lesson_id: "b", lesson_at: "2026-10-08T10:00:00Z", timestamp_sec: 10, note: "Итог урока", status: "mastered", kind: "summary" },
      { lesson_id: "b", lesson_at: "2026-10-08T10:00:00Z", timestamp_sec: 20, note: "Свидетельство", status: "mastered", kind: "evidence" },
      { lesson_id: "c", lesson_at: "2026-09-01T10:00:00Z", timestamp_sec: null, note: "Итог", status: "struggling", kind: "summary" },
      { lesson_id: "d", lesson_at: "2026-08-01T10:00:00Z", timestamp_sec: null, note: "Итог", status: "struggling", kind: "summary" },
    ]);
    expect(history.map((h) => h.lessonId)).toEqual(["b", "a", "c"]);
    expect(history[1]?.status).toBe("developing");
  });
});
