import { useCallback, useRef, useState, type DragEvent, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { errorMessage, type UploadProgress } from "@/api/client";
import { lessonsApi } from "@/api/staff";
import { useStudents, useSubjects, useTeachers } from "@/api/queries";
import { useStaffAuth } from "@/auth/StaffAuth";
import { PageHeader, ProgressBar } from "@/components/ui";
import { datetimeLocalToIso, formatBytes, toDatetimeLocalValue } from "@/lib/format";

const ACCEPT = ".mp4,.mkv,.mov,.webm,.m4a,.mp3,.wav,.ogg,.aac,video/*,audio/*";
const ALLOWED_EXT = ["mp4", "mkv", "mov", "webm", "m4a", "mp3", "wav", "ogg", "aac"];

function fileExtOk(name: string): boolean {
  const ext = name.split(".").pop()?.toLowerCase() ?? "";
  return ALLOWED_EXT.includes(ext);
}

export function UploadPage() {
  const navigate = useNavigate();
  const { user, isTeacher } = useStaffAuth();
  const students = useStudents();
  const teachers = useTeachers();
  const subjects = useSubjects();

  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [studentId, setStudentId] = useState("");
  const [subject, setSubject] = useState("");
  const [teacherId, setTeacherId] = useState(isTeacher && user?.teacher_id ? user.teacher_id : "");
  const [scheduledAt, setScheduledAt] = useState(() => toDatetimeLocalValue(new Date()));
  const [hints, setHints] = useState<{ ru: boolean; kk: boolean }>({ ru: true, kk: true });
  const [consent, setConsent] = useState(false);
  const [progress, setProgress] = useState<UploadProgress | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  const pickFile = useCallback((f: File | undefined | null) => {
    if (!f) return;
    if (!fileExtOk(f.name)) {
      setError("Поддерживаются записи mp4, mkv, mov, webm и аудио m4a, mp3, wav, ogg, aac.");
      return;
    }
    setError(null);
    setFile(f);
  }, []);

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    pickFile(e.dataTransfer.files?.[0]);
  };

  const languageHints = [hints.ru ? "ru" : null, hints.kk ? "kk" : null].filter((h): h is string => Boolean(h));

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!file) {
      setError("Выберите файл записи урока.");
      return;
    }
    if (!studentId) {
      setError("Выберите ученика.");
      return;
    }
    if (!subject) {
      setError("Выберите предмет урока.");
      return;
    }
    if (!teacherId) {
      setError("Выберите преподавателя.");
      return;
    }
    if (!consent) {
      setError("Подтвердите наличие согласия на обработку записи.");
      return;
    }
    if (languageHints.length === 0) {
      setError("Отметьте хотя бы один язык урока.");
      return;
    }
    setBusy(true);
    setError(null);
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const lesson = await lessonsApi.upload(
        {
          file,
          student_id: studentId,
          teacher_id: teacherId || undefined,
          scheduled_at: datetimeLocalToIso(scheduledAt) ?? undefined,
          subject,
          consent_confirmed: true,
          language_hints: languageHints,
        },
        setProgress,
        controller.signal,
      );
      navigate(`/lessons/${lesson.id}`, { replace: true });
    } catch (err) {
      setError(errorMessage(err, "Не удалось загрузить запись."));
      setProgress(null);
    } finally {
      setBusy(false);
      abortRef.current = null;
    }
  };

  const noStudents = students.data && students.data.items.length === 0;

  // The lesson subject defaults to the student's target subject; staff can change it per lesson.
  const pickStudent = (id: string) => {
    setStudentId(id);
    const target = students.data?.items.find((s) => s.id === id)?.target_subject;
    if (target) setSubject(target);
  };
  const subjectOptions = subjects.data ?? [];
  const subjectKnown = subjectOptions.some((s) => s.code === subject);

  return (
    <>
      <PageHeader
        title="Загрузить запись урока"
        subtitle="Видео или аудио одного занятия. После загрузки запись обработается автоматически: расшифровка, кадры, анализ и отчёты."
      />
      <form className="form" onSubmit={submit}>
        <div className="card">
          <div
            className={`dropzone${dragging ? " active" : ""}${file ? " has-file" : ""}`}
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
            onClick={() => inputRef.current?.click()}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") inputRef.current?.click();
            }}
            aria-label="Выбрать файл записи"
          >
            <input ref={inputRef} type="file" accept={ACCEPT} onChange={(e) => pickFile(e.target.files?.[0])} />
            {file ? (
              <>
                <strong>{file.name}</strong>
                <p className="muted small mb-0">{formatBytes(file.size)} · нажмите, чтобы выбрать другой файл</p>
              </>
            ) : (
              <>
                <strong>Перетащите файл сюда или нажмите, чтобы выбрать</strong>
                <p className="muted small mb-0">Запись Zoom / Google Meet: mp4, mkv, mov, webm, а также аудио m4a, mp3, wav</p>
              </>
            )}
          </div>
        </div>

        <div className="card">
          <h3>Данные урока</h3>
          <div className="form-row">
            <div className="field">
              <label htmlFor="student">Ученик</label>
              <select id="student" className="select" required value={studentId} onChange={(e) => pickStudent(e.target.value)}>
                <option value="">— выберите ученика —</option>
                {students.data?.items.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.full_name}
                    {s.grade ? ` · ${s.grade} класс` : ""}
                  </option>
                ))}
              </select>
              {noStudents && (
                <span className="field-hint">
                  Учеников пока нет — <Link to="/students">добавьте ученика</Link>.
                </span>
              )}
            </div>
            <div className="field">
              <label htmlFor="teacher">Преподаватель</label>
              <select
                id="teacher"
                className="select"
                required
                value={teacherId}
                onChange={(e) => setTeacherId(e.target.value)}
                disabled={isTeacher && Boolean(user?.teacher_id)}
              >
                <option value="">— выберите преподавателя —</option>
                {teachers.data?.items.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.full_name}
                  </option>
                ))}
              </select>
              {isTeacher && <span className="field-hint">Урок будет привязан к вам.</span>}
            </div>
            <div className="field">
              <label htmlFor="subject">Предмет</label>
              <select id="subject" className="select" required value={subject} onChange={(e) => setSubject(e.target.value)}>
                <option value="">— выберите предмет —</option>
                {subjectOptions.map((s) => (
                  <option key={s.code} value={s.code}>
                    {s.title}
                  </option>
                ))}
                {subject && !subjectKnown && <option value={subject}>{subject}</option>}
              </select>
              <span className="field-hint">По предмету выбираются правила анализа урока.</span>
            </div>
            <div className="field">
              <label htmlFor="scheduled_at">Дата и время урока</label>
              <input id="scheduled_at" className="input" type="datetime-local" required value={scheduledAt} onChange={(e) => setScheduledAt(e.target.value)} />
            </div>
            <div className="field">
              <span className="field-label">Языки на уроке</span>
              <div className="row" style={{ gap: 16 }}>
                <label className="checkbox">
                  <input type="checkbox" checked={hints.ru} onChange={(e) => setHints((h) => ({ ...h, ru: e.target.checked }))} />
                  Русский
                </label>
                <label className="checkbox">
                  <input type="checkbox" checked={hints.kk} onChange={(e) => setHints((h) => ({ ...h, kk: e.target.checked }))} />
                  Казахский
                </label>
              </div>
              <span className="field-hint">Подсказка для распознавания речи: на уроке часто смешиваются оба языка.</span>
            </div>
          </div>
        </div>

        <div className="card">
          {/* Wording is the vetted staff attestation from docs/CONSENT_TEMPLATE.md §4 — keep the two in sync. */}
          <label className="checkbox">
            <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} required />
            <span>
              <strong>Подтверждаю, что у центра есть действующие письменные согласия законного представителя ученика и преподавателя</strong>{" "}
              на запись этого урока, его автоматизированный анализ системой LessonLens и передачу данных поставщикам
              распознавания речи и модели ИИ, перечисленным в Политике конфиденциальности. Я понимаю, что загрузка записи без
              таких согласий запрещена, а факт подтверждения записывается в журнал действий.
            </span>
          </label>
          <p className="field-hint mt-2 mb-0">
            Согласия хранятся в центре. Шаблон — docs/CONSENT_TEMPLATE.md. Если согласие отозвано — не загружайте запись и
            сообщите ответственному за персональные данные.
          </p>
        </div>

        {error && <div className="form-error" role="alert">{error}</div>}

        {progress && busy && (
          <div className="card">
            <ProgressBar value={progress.ratio} />
            <p className="small muted mt-2 mb-0">
              Загружено {formatBytes(progress.loaded)} из {formatBytes(progress.total)} ({Math.round(progress.ratio * 100)} %)
            </p>
          </div>
        )}

        <div className="row">
          <button type="submit" className="btn btn-primary" disabled={busy || !file || !consent || !studentId || !teacherId || !subject}>
            {busy ? "Загружаем…" : "Загрузить и начать обработку"}
          </button>
          {busy && (
            <button type="button" className="btn" onClick={() => abortRef.current?.abort()}>
              Отменить
            </button>
          )}
          <Link to="/lessons" className="btn btn-ghost">
            К списку уроков
          </Link>
        </div>
      </form>
    </>
  );
}
