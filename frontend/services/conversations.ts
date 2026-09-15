import { get, post } from "./request";

export interface ConversationListResponse {
  conversations?: Array<Record<string, unknown>>;
}

export interface ConversationCreateRequest {
  data_source_id: string;
  adopted_semantic_draft_ids: string[];
  adopted_semantic_draft_titles: string[];
}

export const conversationService = {
  list() {
    return get<ConversationListResponse>("/api/conversations/list");
  },

  detail(conversationId: string, includeMessages = true) {
    return get<Record<string, unknown>>("/api/conversations/detail", {
      query: { conversation_id: conversationId, include_messages: includeMessages },
    });
  },

  create(body: ConversationCreateRequest) {
    return post<Record<string, unknown>>("/api/conversations/create", body);
  },

  delete(conversationId: string) {
    return post<Record<string, unknown>>("/api/conversations/delete", {
      conversation_id: conversationId,
    });
  },

  executionTrace(conversationId: string, turnId: string) {
    return post<Record<string, unknown>>("/api/conversations/execution-trace", {
      conversation_id: conversationId,
      turn_id: turnId,
    });
  },
};
