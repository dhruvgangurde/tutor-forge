import { FormEvent, useRef, useState } from 'react'
import { useUploadCourse } from './hooks'
import { ErrorBanner } from '../../components/ui/ErrorBanner'
import { getErrorMessage } from '../../lib/api/errors'
import { MAX_COURSE_NAME_CHARS } from '../../lib/limits'
import { useToast } from '../../hooks/useToast'

// No legacy .ppt: the backend's parser reads only .pptx (backend/courses/router.py).
export const ALLOWED_EXTENSIONS = '.pdf,.pptx,.txt'

interface CourseUploadFormProps {
  onDone: () => void
}

/** Course upload form: name + multi-file picker. Used inline on CoursesPage. */
export function CourseUploadForm({ onDone }: CourseUploadFormProps) {
  const [name, setName] = useState('')
  const fileInputRef = useRef<HTMLInputElement>(null)
  const upload = useUploadCourse()
  const { showToast } = useToast()

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    const files = Array.from(fileInputRef.current?.files ?? [])
    if (files.length === 0) return

    try {
      await upload.mutateAsync({ name, files })
      showToast('Course uploaded — ingestion started.', 'success')
      setName('')
      if (fileInputRef.current) fileInputRef.current.value = ''
      onDone()
    } catch {
      // Error is surfaced via upload.isError below; nothing further to do here.
    }
  }

  return (
    <form onSubmit={handleSubmit} className="login-form">
      {upload.isError && <ErrorBanner message={getErrorMessage(upload.error)} />}

      <div className="field-group">
        <label htmlFor="course-name-input" className="field-label">
          Course name
        </label>
        <input
          id="course-name-input"
          type="text"
          className="field-input"
          value={name}
          onChange={(e) => setName(e.target.value)}
          required
          maxLength={MAX_COURSE_NAME_CHARS}
          placeholder="e.g. Biology 101"
        />
      </div>

      <div className="field-group">
        <label htmlFor="course-files-input" className="field-label">
          Course materials (PDF, PPTX, TXT)
        </label>
        <input
          id="course-files-input"
          ref={fileInputRef}
          type="file"
          className="field-input"
          multiple
          accept={ALLOWED_EXTENSIONS}
          required
        />
      </div>

      <button type="submit" className="btn btn-primary" disabled={upload.isPending}>
        {upload.isPending ? 'Uploading…' : 'Upload course'}
      </button>
    </form>
  )
}
