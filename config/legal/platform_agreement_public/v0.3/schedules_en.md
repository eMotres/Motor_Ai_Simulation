# Schedules to the AeroStator Platform Services and Data Agreement

Version {{version}}. These Schedules form part of the AeroStator Platform Services and Data Agreement (the "Agreement"). Defined terms have the meaning given in the Agreement. The English text governs; the Chinese text is a full mirror.

## Schedule 1 — Data Processing Addendum

S1.1 **Scope and roles.** This Schedule applies where MOTRES processes Personal Data on the Client's behalf in providing project and collaboration functions (Agreement section 8.5). The Client is controller, or processor for its own client, and MOTRES is processor or subprocessor. For account administration, billing, fraud prevention, security and legal compliance MOTRES is an independent controller under the Privacy Notice, and this Schedule does not apply to that processing.

S1.2 **Subject matter, duration, nature and purpose.** Hosting, storage, computation, display, sharing with Authorised Recipients, support and backup of project data for the term of the Agreement and the deletion periods in Agreement section 11. No Personal Data is used for AI training (Agreement section 7.3). MOTRES removes such Personal Data before training as an independent controller under the Privacy Notice.

S1.3 **Data subjects and data.** Users and Authorised Recipients, and persons named in project data: names, business e-mail addresses, company and job title, account and access records, messages and comments in projects, and names in documents or file metadata. The Client shall not upload special categories of personal data or data on criminal convictions.

S1.4 **Instructions.** MOTRES processes the Personal Data only on the Client's documented instructions, including with regard to transfers of Personal Data to a third country, which are the Agreement, the Client's use and settings of the Platform, and further written instructions within its scope, unless EU or Member State law requires otherwise; MOTRES then informs the Client unless the law prohibits it. MOTRES informs the Client if it considers an instruction unlawful.

S1.5 **Confidentiality of personnel.** Persons authorised to process the Personal Data are bound by confidentiality.

S1.6 **Security measures.** MOTRES applies the following measures and keeps them under review: HTTPS (TLS) for all access to the Platform; password hashing and scoped, revocable session, API and agent tokens; role- and project-based access control with logical separation of workspaces and projects; audited administrator access; least-privilege staff access; hosting in EU data centres; hourly encrypted backups (restic) to a separate EU storage service with the rotation hourly for 24 hours, daily for 30 days and monthly for 12 months; a register of deleted accounts that is re-applied after any restore; security logging with pseudonymised identifiers after account deletion. MOTRES does not claim certifications it does not hold.

S1.7 **Subprocessors.** The Client gives general written authorisation to the subprocessors in Schedule 2 that process Personal Data on its behalf. Agreement section 8.3 governs notice of additions or replacements and objections. MOTRES imposes on each subprocessor data-protection obligations equivalent to this Schedule and remains liable for their performance as required by law.

S1.8 **Assistance.** Taking into account the nature of the processing, MOTRES assists the Client by appropriate measures in responding to data-subject requests, and with security, breach notification, data-protection impact assessments and prior consultation, using the information available to it.

S1.9 **Personal-data breaches.** MOTRES notifies the Client without undue delay after becoming aware of a personal-data breach affecting the Client's Personal Data, with the information then available, and supplements it as further information becomes available.

S1.10 **Return and deletion.** At the end of the processing the Client may export its data under Schedule 3; MOTRES then deletes the Personal Data within the periods of Agreement section 11.5, unless the law requires storage.

S1.11 **Audit and information.** MOTRES makes available the information necessary to demonstrate compliance with this Schedule and allows for and contributes to audits by the Client or an auditor mandated by it, on reasonable notice, during business hours, at the Client's cost, at most once a year unless a breach or a regulator requires otherwise, and subject to confidentiality and the protection of other clients' data.

S1.12 **International transfers.** Where the Client is established outside the EEA in a country without an adequacy decision, the EU Standard Contractual Clauses (Commission Implementing Decision (EU) 2021/914), Module 4 (processor to controller), are incorporated by reference between MOTRES as data exporter and the Client as data importer. For onward transfers to subprocessors in Schedule 2, MOTRES relies on an adequacy decision, including the EU–US Data Privacy Framework for certified providers, or concludes Module 3 with the subprocessor, with the required transfer assessment. Annex I is completed by the acceptance record and S1.2–S1.3, Annex II by S1.6 and Annex III by Schedule 2. The governing law and courts are those of Slovenia, and the competent supervisory authority, where required, is the Information Commissioner of the Republic of Slovenia. The UK Addendum and Swiss adaptations apply where those laws apply.

## Schedule 2 — Service Providers

S2.1 MOTRES uses the providers below. "Project content" means Engineering Data and project messages. No provider may use project content or assistant conversations to train its models or to improve its own services (Agreement section 8.2).

| Provider (contracting entity) | Service | Data | Location | Role |
|---|---|---|---|---|
| Hetzner Online GmbH, Gunzenhausen, Germany | Dedicated servers that host the Platform, its databases and compute | All Platform data | Germany (EU) | Subprocessor / hosting |
| Hetzner Online GmbH | Storage Box for encrypted backups | Encrypted backups of all Platform data | EU | Subprocessor / backup |
| Google Ireland Limited (Google Workspace) | E-mail for support@aerostator.com and system mail; storage of MOTRES's internal engineering dataset | Mail content and addresses; training records cleaned under Agreement sections 7.3 and 7.10 | EU and other Google locations, including the United States | Subprocessor (mail); MOTRES storage (dataset) |
| Google LLC (Google Sign-In) | Optional sign-in with a Google account | Name, e-mail, Google account identifier | United States | Independent controller for the Google account |
| Google LLC (Google Analytics) | Website statistics, only after cookie consent and never for visitors in Mainland China, Hong Kong or Macau or in Chinese interface mode | Cookie identifiers, usage events | United States | Processor for statistics |
| Anthropic, PBC (Claude API, commercial terms) | Help assistant, specification assistant and design assistant | Assistant messages, the page context and the project content needed for the answer | United States | Subprocessor; no training on inputs or outputs |
| OpenRouter, Inc., routing only to endpoints that, under the account's data-policy setting, neither train on nor retain inputs | Machine translation of production texts between English and Chinese with a DeepSeek model | Production texts entered for translation | United States and the endpoint's country, recorded by MOTRES and given on request; never Mainland China | Subprocessor; no training on inputs |
| MOTRES internal engineering tools (Anthropic, OpenAI, Google) | Engineering work by MOTRES staff on a Client's project at the Client's request | Project content needed for that work; never Personal Data or Protected Data unless the tool is used under business or API terms with a data processing agreement | United States | Subprocessor where business terms apply; training on submitted content excluded |

S2.2 The Platform's servers, databases and backups are in Germany (EU). Project content is also processed by the providers above in the United States, and MOTRES's internal engineering dataset is stored in Google Workspace (EU and other Google locations, including the United States). No data are hosted in Mainland China (Agreement section 14.5). Transfers to the United States rely on the EU–US Data Privacy Framework where the provider is certified, and otherwise on the Standard Contractual Clauses (Schedule 1, S1.12).

S2.3 Changes to this Schedule follow Agreement section 8.3. The current version is published at aerostator.com together with the Agreement.

## Schedule 3 — Export Register

S3.1 **Self-service export (account menu, "Download my data").** A ZIP archive by a signed, single-use link: account record, consents and communication preferences, sessions, API and agent keys, authorisation grants, authentication and agent-tool audit records, support requests, usage records, administrator-access records, and the whole personal workspace and published folders, including machine configurations, operating points and saved simulation results, in their native YAML, JSON, JSONL and CSV files.

S3.2 **Export on request.** Development projects (specifications, threads, project files, reports and Results stored in projects), production records and anything not covered by S3.1 are exported on written request to support@aerostator.com, in their native formats (JSON, YAML, CSV, PDF, STEP and the uploaded file formats), within 30 days and, in a switching process, within the transitional period of Agreement section 11.7. The Client may also read its data through the Platform's API and agent tools.

S3.3 **Excluded categories.** Model Materials supplied by MOTRES under the Model Access NDA and restricted MOTRES library content are withheld from exports and marked as withheld; MOTRES model weights, internal training datasets and other MOTRES Technology are not exportable. These exclusions do not obstruct switching of the Client's own data.

S3.4 **Infrastructure and safeguards.** Infrastructure jurisdictions: Germany (EU) for the Platform's servers, databases and backups; the United States and the EU for the providers in Schedule 2. MOTRES will resist conflicting foreign-government access requests that lack a valid legal basis under EU law, disclose only what is required (Agreement section 5.4) and inform the Client where legally permitted.

S3.5 Export, switching and egress are free of charge (Agreement section 11.6).

S3.6 The structures and field descriptions of the exported files are published on the Platform together with this Schedule and kept up to date.
