export interface ConfigureContextScope {
  motorId: string | null;
  identity: string | null;
}

export interface ConfigureContextRequest {
  generation: number;
  scope: ConfigureContextScope;
}

export function contextForIdentity<T>(
  contextIdentity: string | null,
  currentIdentity: string | null,
  context: T | null,
): T | null {
  return contextIdentity === currentIdentity ? context : null;
}

/** Reject stale responses after a Configure reference, account, or refresh changes. */
export class ConfigureContextRequestGuard {
  private generation = 0;

  begin(scope: ConfigureContextScope): ConfigureContextRequest {
    this.generation += 1;
    return { generation: this.generation, scope: { ...scope } };
  }

  invalidate(): void {
    this.generation += 1;
  }

  accepts(
    request: ConfigureContextRequest,
    currentScope: ConfigureContextScope,
    currentIdentity: string | null,
    responseMotorId: string | null,
  ): boolean {
    return request.generation === this.generation
      && request.scope.motorId === currentScope.motorId
      && request.scope.identity === currentScope.identity
      && currentIdentity === request.scope.identity
      && responseMotorId === request.scope.motorId;
  }
}
