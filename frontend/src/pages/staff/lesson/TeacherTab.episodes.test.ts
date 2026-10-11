/** Episode attribution mirrors backend/app/pipeline/reports.py build_episodes. */
import { describe, expect, it } from "vitest";
import type { LessonAnalysis, StudentError, TeachingEvent } from "@/types/analysis";
import { episodesFromAnalysis, episodesFromReport } from "./TeacherTab";

function err(timestamp: number, skill_key: string, extra: Partial<StudentError> = {}): StudentError {
  return {
    timestamp,
    skill_key,
    skill_name: skill_key,
    description: `ошибка ${skill_key}`,
    quote: null,
    frame_ids: [],
    repeated_after_explanation: false,
    corrected: true,
    ...extra,
  };
}

function reExplanation(start: number): TeachingEvent {
  return { start, end: start + 30, kind: "re_explanation", speaker: "teacher", summary: `переобъяснение ${start}`, quote: null, frame_ids: [] };
}

function analysis(errors: StudentError[], timeline: TeachingEvent[]): LessonAnalysis {
  return {
    schema_version: "1.0",
    subject: "Русский язык",
    topic: "Тест",
    lesson_summary: "",
    learning_objectives: [],
    timeline,
    independent_successes: [],
    errors,
    misconceptions: [],
    skills: [],
    visual_observations: [],
    teacher_recommendations: [],
    manager_indicators: null,
    parent_report: { topic_plain: "", what_child_did_well: [], what_was_hard: [], what_to_repeat: [], home_task_suggestion: null, encouragement: "", progress_note: null },
    limitations: [],
    language_notes: null,
  };
}

describe("episodesFromAnalysis", () => {
  it("does not chain a re-explanation of another skill to an earlier error", () => {
    // demo lesson shape: padezh slip at 94 s, kuda error at 860 s, kuda re-explanation at 862 s
    const a = analysis([err(94.1, "padezhnye_okonchaniya"), err(860, "kuda_vs_gde")], [reExplanation(862.5)]);
    const [padezh, kuda] = episodesFromAnalysis(a);
    expect(padezh?.reExplanation).toBeNull();
    expect(padezh?.repeatedAt).toBeNull();
    expect(kuda?.reExplanation?.timestamp).toBe(862.5);
  });

  it("claims a repeat only after the attributed re-explanation and keeps corrected visible", () => {
    const a = analysis(
      [err(969, "kuda_vs_gde"), err(1145, "kuda_vs_gde", { repeated_after_explanation: true }), err(1311, "kuda_vs_gde", { corrected: true })],
      [reExplanation(1157)],
    );
    const [first, second, last] = episodesFromAnalysis(a);
    expect(first?.reExplanation?.timestamp).toBe(1157);
    expect(first?.repeatedAt).toBe("21:51"); // 1311 s — after the re-explanation, not 1145 s before it
    expect(second?.repeatedAfterExplanation).toBe(true);
    expect(last?.reExplanation).toBeNull();
    expect(last?.repeatedAt).toBeNull();
    expect(last?.corrected).toBe(true);
  });
});

describe("episodesFromReport", () => {
  it("reads the backend episode chain and falls back to null when absent", () => {
    const eps = episodesFromReport({
      episodes: [
        {
          timestamp: 27,
          skill_name: "-тся/-ться",
          description: "учиться",
          quote: null,
          frames: [{ index: 1 }],
          re_explanation: { timestamp: 31, summary: "проверка вопросом" },
          repeated_at: "02:00",
          repeated_after_explanation: false,
          corrected: true,
        },
      ],
    });
    expect(eps).toHaveLength(1);
    expect(eps?.[0]).toMatchObject({ frameIds: [1], repeatedAt: "02:00", corrected: true });
    expect(eps?.[0]?.reExplanation).toEqual({ timestamp: 31, summary: "проверка вопросом" });
    expect(episodesFromReport({})).toBeNull();
    expect(episodesFromReport(undefined)).toBeNull();
  });
});
