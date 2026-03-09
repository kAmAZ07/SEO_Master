export interface AuditRequest {
  url: string;
}

export interface AuditStatus {
  uid: string;
  status: 'pending' | 'processing' | 'completed' | 'failed';
  progress: number;
  result?: AuditResult;
  error?: string;
}

export interface AuditResult {
  url: string;
  issues: AuditIssue[];
  cwv: CoreWebVitals;
  meta: MetaAnalysis;
  links: LinkAnalysis;
  schema: SchemaAnalysis;
  timestamp: string;
}

export interface AuditIssue {
  severity: 'critical' | 'warning' | 'info';
  category: string;
  title: string;
  description: string;
  recommendation: string;
}

export interface CoreWebVitals {
  lcp: number;
  fid: number;
  cls: number;
  score: number;
}

export interface MetaAnalysis {
  title: {
    value: string;
    length: number;
    issues: string[];
  };
  description: {
    value: string;
    length: number;
    issues: string[];
  };
  h1: {
    value: string;
    count: number;
    issues: string[];
  };
}

export interface LinkAnalysis {
  total: number;
  internal: number;
  external: number;
  broken: number;
  brokenLinks: string[];
}

export interface SchemaAnalysis {
  hasSchema: boolean;
  types: string[];
  issues: string[];
}
