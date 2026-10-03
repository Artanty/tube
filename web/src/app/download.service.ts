import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { FormatsResponse, Task, TasksResponse } from './models';

@Injectable({ providedIn: 'root' })
export class DownloadService {
  private readonly http = inject(HttpClient);

  start(url: string, formatId: string | null): Observable<Task> {
    return this.http.post<Task>('/api/download', { url, format_id: formatId });
  }

  list(): Observable<TasksResponse> {
    return this.http.get<TasksResponse>('/api/tasks');
  }

  formats(url: string): Observable<FormatsResponse> {
    return this.http.post<FormatsResponse>('/api/formats', { url });
  }
}