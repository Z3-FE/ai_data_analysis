export interface Conversation {
  id: string;
  title: string;
  date: string;
  active?: boolean;
}

export type ConversationStatus = 'created' | 'idle' | 'running' | 'waiting_user' | 'completed' | 'failed' | 'cancelled';

export interface ConversationDetail extends Conversation {
  threadId: string;
  status: ConversationStatus;
  dataSource: string;
  dataSourceType: string;
  createdAt: string;
  updatedAt: string;
  lastMessagePreview?: string;
  adoptedSemanticDraftIds: string[];
  adoptedSemanticDraftTitles: string[];
}

export type ConversationMessageRole = 'user' | 'assistant' | 'system' | 'tool';

export interface ConversationMessage {
  id: string;
  conversationId: string;
  role: ConversationMessageRole;
  content: string;
  createdAt: string;
  metadata?: {
    step?: string;
    toolName?: string;
    status?: string;
  };
}

export interface Metric {
  id: string;
  name: string;
  definition: string;
  sqlExpression: string;
  dimensions: string[];
  source: string;
  status: 'published' | 'pending'; // 已发布 | 待审核
  updatedAt: string;
  metricId: string; // e.g., sales_amount
  dependencies: string[]; // dependent tables e.g. orders, order_items
  synonyms: string[];
  sampleQuestion: string;
}

export interface Dimension {
  id: string;
  name: string;
  fieldMapping: string;
  dataset: string;
  type: string; // e.g., 地理维度, 时间维度, 分类维度
  availableMetrics: string[];
  source: string;
  status: 'published' | 'pending'; // 已发布 | 待审核
  updatedAt?: string;
  commonFilter?: string; // e.g. 非空地区
  synonyms: string[];
  sampleQuestion: string;
}

export interface Glossary {
  id: string;
  name: string;
  type: string; // e.g., 指标别名, 维度别名, 分组规则
  mappingTarget: string; // e.g. 销售额, 客户地区, 订单状态
  explanation: string;
  source: string;
  status: 'published' | 'pending'; // 已发布 | 待审核
  lastUsed: string;
  conflictCheck?: string; // e.g. 无冲突
  agentResult?: string; // e.g. GMV -> 销售额
  sampleQuestion?: string;
}

export interface DbField {
  name: string;
  description: string;
  type: string;
  status: string; // e.g. 已命中语义层
}

export interface DbTable {
  name: string;
  description: string;
  primaryKey: string;
  coreFields: string[];
  fields: DbField[];
  queryAllowed: boolean;
  dependentRelations: string[];
  commonTimeField: string;
}

export interface StepDetail {
  id: string;
  name: string;
  status: 'completed' | 'running' | 'pending';
  duration?: string;
  description?: string;
}

export type SemanticUploadCategory = 'metric' | 'glossary';

export type SemanticDraftKind = 'metric' | 'glossary' | 'dimension' | 'business_rule';

export type SemanticDraftStatus = 'pending' | 'adopted' | 'rejected' | 'published';

export interface SemanticDraftField {
  label: string;
  value: string;
}

export interface SemanticDraft {
  id: string;
  kind: SemanticDraftKind;
  status: SemanticDraftStatus;
  title: string;
  description: string;
  mappingTarget: string;
  confidence: number;
  sourceDocument: string;
  sourceSnippet: string;
  fields: SemanticDraftField[];
  adoptedScope?: 'session' | 'global';
  updatedAt?: string;
  vectorStatus?: string;
}

export interface SemanticUploadTask {
  id: string;
  fileName: string;
  fileSize: string;
  category: SemanticUploadCategory;
  status: 'uploading' | 'extracting' | 'ready' | 'failed';
  progress: number;
  message: string;
}
