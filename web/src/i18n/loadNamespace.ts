/** Keep failed locale imports rejected while notifying the recovery controller. */
export function loadNamespace<T>(
  load: () => Promise<T>,
  onFailure: (error: unknown) => void,
): Promise<T> {
  return load().catch((error: unknown) => {
    onFailure(error);
    throw error;
  });
}
