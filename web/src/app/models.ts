export interface Task {
  id: string;
  url: string;
  format_id: string | null;
  status: 'queued' | 'downloading' | 'completed' | 'error';
  phase: string | null;
  error: string | null;
  title: string | null;
  duration: number | null;
  thumbnail: string | null;
  filename: string | null;
  filesize: number | null;
  downloaded_bytes: number;
  total_bytes: number | null;
  downloaded_human: string | null;
  total_human: string | null;
  speed: number | null;
  speed_human: string | null;
  eta: number | null;
  percent: number;
  created_at: number;
  finished_at: number | null;
  finished: boolean;
  file_url: string | null;
}

export interface FormatOption {
  format_id: string;
  ext: string | null;
  resolution: string | null;
  height: number | null;
  fps: number | null;
  vcodec: string | null;
  acodec: string | null;
  filesize: number | null;
  filesize_human: string | null;
  note: string | null;
  has_video: boolean;
  has_audio: boolean;
}

export interface VideoInfo {
  title: string | null;
  duration: number | null;
  thumbnail: string | null;
  uploader: string | null;
}

export interface FormatsResponse {
  video: VideoInfo;
  formats: FormatOption[];
}

export interface TasksResponse {
  tasks: Task[];
}