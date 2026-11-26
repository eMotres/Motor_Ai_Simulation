# Gears: Saved gearboxes

The panel at the top of the left column. A record keeps the design, the operating point or duty spectrum, and the last result when it is current.

Steps:
- Name: type a name, optionally a Project id (blank keeps the record in the workspace; there is no access rule yet, the page is staff only).
- Save stores the current design as a new record, taking the open Point or Duty spectrum request. Save changes overwrites the open record; it is refused when somebody saved a newer version meanwhile ("Changed elsewhere. Reload to continue.").
- The list shows the records: Open loads one into the editor (and its result when it is current), Archive moves it out of the list, Restore brings an archived one back (turn on Archived to see them). Reload re-reads the list.
- An open record can be the Gearbox source in With motor.

Flags on a record: Result outdated (the saved result is not shown on Open; calculate again), Hash mismatch, Request invalid, Checksum mismatch, Core changed, Catalogue changed. Files that could not be read are counted as unreadable and not listed; ask an administrator.

A record that the editor cannot represent shows an error when opened and is not loaded.
