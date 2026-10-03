import { Component, OnDestroy, computed, effect, inject, signal } from '@angular/core';
import { DecimalPipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { DownloadService } from './download.service';
import { FormatOption, Task, VideoInfo } from './models';

const POLL_MS = 1000;

@Component({
  selector: 'app-root',
  imports: [FormsModule, DecimalPipe],
  templateUrl: './app.component.html',
  styleUrl: './app.component.css',
})
export class AppComponent implements OnDestroy {
  private readonly api = inject(DownloadService);

  readonly url = signal('');
  readonly formatId = signal<string | null>(null);
  readonly formats = signal<FormatOption[]>([]);
  readonly video = signal<VideoInfo | null>(null);
  readonly tasks = signal<Task[]>([]);
  readonly submitting = signal(false);
  readonly probing = signal(false);
  readonly error = signal<string | null>(null);

  readonly canSubmit = computed(() => this.url().trim().length > 0 && !this.submitting());
  readonly hasActive = computed(() => this.tasks().some((t) => !t.finished));

  private poller: ReturnType<typeof setInterval> | null = null;

  constructor() {
    this.refresh();
    effect(() => {
      if (this.hasActive()) {
        this.startPolling();
      } else {
        this.stopPolling();
      }
    });
  }

  ngOnDestroy(): void {
    this.stopPolling();
  }

  submit(): void {
    const url = this.url().trim();
    if (!url || this.submitting()) {
      return;
    }
    this.submitting.set(true);
    this.error.set(null);
    this.api.start(url, this.formatId()).subscribe({
      next: (task) => {
        this.tasks.update((list) => [task, ...list.filter((t) => t.id !== task.id)]);
        this.url.set('');
        this.formats.set([]);
        this.video.set(null);
        this.formatId.set(null);
      },
      error: (err) => this.error.set(this.describe(err)),
      complete: () => this.submitting.set(false),
    });
  }

  inspect(): void {
    const url = this.url().trim();
    if (!url || this.probing()) {
      return;
    }
    this.probing.set(true);
    this.error.set(null);
    this.api.formats(url).subscribe({
      next: (res) => {
        this.video.set(res.video);
        this.formats.set(res.formats);
        this.formatId.set(this.pickDefault(res.formats));
      },
      error: (err) => this.error.set(this.describe(err)),
      complete: () => this.probing.set(false),
    });
  }

  remove(id: string): void {
    this.tasks.update((list) => list.filter((t) => t.id !== id));
  }

  retry(task: Task): void {
    this.url.set(task.url);
    this.submit();
  }

  /**
   * Prefer a format that already has both streams. YouTube lists every video-only format above the
   * combined ones, so picking the highest resolution blindly used to pre-select a silent 4K stream.
   * The backend merges audio in for video-only picks, but choosing a muxed format avoids the
   * download-then-merge round trip entirely.
   */
  private pickDefault(formats: FormatOption[]): string | null {
    const usable = formats.filter((f) => f.has_video || f.has_audio);
    const muxed = usable.filter((f) => f.has_video && f.has_audio);
    const pool = muxed.length ? muxed : usable;
    return pool.reduce<FormatOption | null>(
      (best, f) => (best === null || (f.height ?? 0) > (best.height ?? 0) ? f : best),
      null,
    )?.format_id ?? null;
  }

  formatLabel(f: FormatOption): string {
    const size = f.filesize_human ? ` · ${f.filesize_human}` : '';
    const fps = f.fps && f.fps > 30 ? `${f.fps}fps` : '';
    const audio = f.has_audio ? '' : ' · +audio';
    return `${f.resolution ?? f.format_id}${fps ? ' ' + fps : ''} · ${f.ext ?? ''}${audio}${size}`;
  }

  statusLabel(task: Task): string {
    if (task.status === 'completed') {
      return 'Done';
    }
    if (task.status === 'error') {
      return 'Failed';
    }
    if (task.status === 'queued') {
      return 'Queued';
    }
    if (task.phase === 'processing') {
      return 'Merging audio';
    }
    if (task.phase === 'checking') {
      return 'Verifying';
    }
    return 'Downloading';
  }

  etaLabel(task: Task): string {
    if (!task.eta || task.eta <= 0) {
      return '';
    }
    const s = Math.round(task.eta);
    if (s < 60) {
      return `${s}s left`;
    }
    const m = Math.floor(s / 60);
    const rest = s % 60;
    return rest ? `${m}m ${rest}s left` : `${m}m left`;
  }

  trackTask(_i: number, task: Task): string {
    return task.id;
  }

  trackFormat(_i: number, f: FormatOption): string {
    return f.format_id;
  }

  private refresh(): void {
    this.api.list().subscribe({
      next: (res) => this.tasks.set(res.tasks),
      error: (err) => this.error.set(this.describe(err)),
    });
  }

  private startPolling(): void {
    if (this.poller !== null) {
      return;
    }
    this.poller = setInterval(() => {
      this.api.list().subscribe({
        next: (res) => this.tasks.set(res.tasks),
        error: () => undefined,
      });
    }, POLL_MS);
  }

  private stopPolling(): void {
    if (this.poller !== null) {
      clearInterval(this.poller);
      this.poller = null;
    }
  }

  private describe(err: unknown): string {
    const e = err as { error?: { error?: string }; message?: string };
    return e?.error?.error ?? e?.message ?? 'Request failed';
  }
}