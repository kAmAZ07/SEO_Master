export interface HITLTask {
  id: string;
  projectId: string;
  url: string;
  changeType: string;
  oldContent: Record<string, unknown>;
  newContent: Record<string, unknown>;
  priority: number;
  impact: number;
  effort: number;
  status: 'pending' | 'approved' | 'rejected';
  createdAt: string;
  expiresAt: string;
  metadata: {
    sagaId: string;
    correlationId: string;
    ffscore?: number;
    eeatScore?: number;
  };
}

export interface HITLApproval {
  taskId: string;
  approved: boolean;
  comment?: string;
}
