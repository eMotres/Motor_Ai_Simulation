/** Commit a number only when the user edited it and the field is writable. */
export function shouldCommitConfigureNumber(edited: boolean, readOnly: boolean): boolean {
  return edited && !readOnly;
}
