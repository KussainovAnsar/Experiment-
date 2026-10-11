/** /parent/students/:id/lessons/:lessonId — full parent report. */
import { useParams } from "react-router-dom";
import { useParentLesson } from "@/api/queries";
import { ParentReportView } from "@/components/ParentReportView";
import { BackLink, ErrorState, Skeleton } from "@/components/ui";

export function ParentLessonPage() {
  const { id = "", lessonId = "" } = useParams();
  const q = useParentLesson(id, lessonId);
  return (
    <>
      <BackLink to={`/parent/students/${id}`}>Все уроки</BackLink>
      <div className="mt-4">
        {q.isLoading && <Skeleton lines={8} />}
        {q.isError && <ErrorState error={q.error} onRetry={() => void q.refetch()} />}
        {q.data && (
          <ParentReportView data={q.data.data} frames={q.data.frames} realm="parent" teacherName={q.data.teacher_name} scheduledAt={q.data.scheduled_at} />
        )}
      </div>
    </>
  );
}
