import { get, post, postStream, type PostOptions } from "./request";

export interface HarnessRunRequest {
  input_text: string;
  user_id: string;
  conversation_id?: string;
  asset_ids?: string[];
}

export interface HarnessResumeRequest {
  confirmation_id: string;
  answer: string;
  decision: "confirm" | "reject";
  resolved_conditions?: Record<string, unknown>;
}

export interface HarnessEvent {
  type: string;
  event_type?: string;
  payload?: Record<string, unknown>;
  run_ref?: {
    user_id?: string;
    conversation_id?: string;
    thread_id?: string;
    turn_id?: string;
    run_id?: string;
  };
  [key: string]: unknown;
}

export const harnessService = {
  run(body: HarnessRunRequest) {
    return post<Record<string, unknown>>("/api/harness/run", body);
  },

  streamRun(body: HarnessRunRequest, options?: PostOptions) {
    return postStream<HarnessEvent>("/api/harness/run/stream", body, options);
  },

  status(runId: string, userId: string) {
    return get<Record<string, unknown>>("/api/harness/run/status", {
      query: { run_id: runId, user_id: userId },
    });
  },

  resume(body: HarnessResumeRequest & { run_id: string; user_id: string }) {
    return post<Record<string, unknown>>("/api/harness/run/resume", body);
  },

  streamResume(
    body: HarnessResumeRequest & { run_id: string; user_id: string },
    options?: PostOptions,
  ) {
    return postStream<HarnessEvent>("/api/harness/run/resume/stream", body, options);
  },
};
