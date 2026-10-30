# AeroStator Platform Services and Data Agreement

Version {{version}}. The English and Chinese texts are presented together; if they differ, the English text prevails, subject to mandatory law.

**Parties.** MOTRES d.o.o., registration no. 7255691000, Kotnikova 34, 1000 Ljubljana, Slovenia ("MOTRES"); and **{{company}}**, registration no. {{reg_no}}, incorporated in {{country}}, registered address {{address}} (the "Client"), represented by {{signatory}}, {{title}}, e-mail {{email}}. Accepted online on {{date}}. Selected mode: **{{mode}}** (FREE or PRIVATE). MOTRES's notice e-mail is support@aerostator.com.

**Important terms** (summary; the sections govern): business use only; all simulations on the Platform are free of charge; your projects are confidential in both modes (section 5); in FREE mode MOTRES may use your engineering data, including data you supplied before accepting, to train its own internal AI models, without payment or other obligations to you for that use, and models already trained are kept (section 7); in PRIVATE mode (paid) nothing you supply or generate, and nothing derived from it, is used for any AI training (section 6); no personal data is used for training; publication in the community dataset needs a separate optional consent (section 7.8); the Platform and all Results are provided "as is", and MOTRES excludes liability for them except where the law does not allow it, while its liability for its data obligations is capped (sections 9–10); liability for a product arises only under a separate paid agreement for that project (section 10.2); export and switching to another provider are free of charge (section 11); Results must be independently verified before manufacture or safety-critical use; the court or arbitration clause is in section 13.

## 1. Definitions

1.1 **Platform**: the AeroStator engineering platform at aerostator.com and its interfaces (web, API, agent tools), operated by MOTRES.

1.2 **Client Data**: everything the Client or its Users upload or enter into the Platform, for example geometry, CAD models, materials, winding schemes, requirements, specifications, test measurements, documents and settings.

1.3 **Results**: outputs generated for the Client's projects, for example FEM and other simulation results, field data, meshes, optimisation histories, design variants, reports, charts and derived engineering data.

1.4 **Engineering Data**: Client Data and Results, excluding Personal Data.

1.5 **Client Confidential Information**: Client Data, Results, the existence of the Client's projects and non-public business information of the Client disclosed in providing the service, including Personal Data, subject to section 5.5.

1.6 **Personal Data**: information relating to an identified or identifiable natural person, such as names, e-mail addresses and login records of Users.

1.7 **Users**: individuals the Client authorises to use the Platform on its behalf.

1.8 **Authorised Recipients**: individuals or entities to which the Client grants project or production access through the Platform's sharing functions, or which it names in writing, for example its engineers, collaborators, manufacturers and OEM producers.

1.9 **MOTRES Technology**: the Platform, its software, models, methods, algorithms, material and component libraries, catalogue motors, AeroStator Core motor technology, know-how and documentation, including improvements of them, held by MOTRES or its licensors.

1.10 **Internal AI Training**: training, fine-tuning or evaluating AI or machine-learning models that MOTRES controls, or collecting or building datasets for them.

1.11 **Protected Data**: Engineering Data supplied or generated while the Client is in PRIVATE mode, or treated as Protected Data under section 11.1, together with every copy, derivative, rerun, re-upload, optimisation, report or other data that contains, is generated from or reveals it, and every dataset that mixes it with other data. Data subject to another no-training obligation are also Protected Data. Mixed data inherit the most restrictive applicable permission.

1.12 **Order**: the order accepted by the Client for PRIVATE mode, stating the fee, service period, renewal mechanism and any other agreed terms.

1.13 **Model Access NDA**: the AeroStator Core Model Access and Non-Disclosure Agreement in the version accepted by the Client. **Model Materials** has the meaning given there.

1.14 **Schedules**: Schedule 1 — Data Processing Addendum, Schedule 2 — Service Providers and Schedule 3 — Export Register, each in the version displayed before acceptance and identified in the acceptance record, form part of this Agreement. The AeroStator Privacy Policy (the "Privacy Notice") is displayed before acceptance.

1.15 **Terms of Use**: the AeroStator Terms of Use accepted when an account is created or when an existing account holder is asked to accept them.

1.16 **Data Sharing Consent**: the separate, optional consent by which a Client or User agrees that data are included in the engineering dataset shared with the AeroStator community.

## 2. Scope, business use and who accepts

2.1 MOTRES provides access to the Platform for the design, simulation, optimisation and analysis of electric machines and related components (for example controllers, propellers and drives) and for project and production collaboration with Authorised Recipients.

2.2 The Platform is offered to businesses only. The signatory confirms that the Client is a company or a registered sole trader acting for business purposes, that the details given are correct and that the signatory has authority to bind the Client. No personal consumer account may be opened. MOTRES may require corporate verification before granting access or specific functions.

2.3 **Account acceptance.** An account holder who accepts the Terms of Use thereby accepts this Agreement in FREE mode (a) for the business that it names and confirms being authorised to bind, or (b) where no business is named, for its own business as a sole trader or self-employed professional, in which case "Client" means the account holder. An account holder who uses the Platform for an employer or customer without authority to bind it may upload in FREE mode only data that it is entitled to license under section 7.6. An account acceptance never selects PRIVATE.

2.4 **Company acceptance.** A company accepts through an authorised representative in the acceptance dialog, with the mode selection under section 3.4. From that acceptance it covers all Users confirmed for that company (by a verified e-mail address in a domain registered by the company, or by invitation of its administrator) and replaces their account acceptances for all Engineering Data they supply or generate on the Platform, including in their personal workspaces. If the company selects PRIVATE, section 11.1 applies to all those data, including data supplied earlier under account acceptances.

2.5 The Client is responsible for its Users and their compliance with this Agreement, and keeps their access credentials secure.

## 3. Modes and fees

3.1 **FREE**: no fee. All simulations, including FEM, optimisation and the other engineering functions offered on the Platform, are free of charge within the compute limits and queues the Platform applies to all FREE users. Projects are confidential (section 5). MOTRES may use eligible Engineering Data for Internal AI Training under section 7.

3.2 **PRIVATE**: fee as stated in the accepted Order. Projects are confidential (section 5), and Protected Data is excluded from all AI training under section 6. The PRIVATE fee is consideration solely for the commitments in section 6; it does not purchase any engineering service, warranty or service level.

3.3 Both modes provide the same engineering functions and solver methods. For identical inputs, solver versions and numerical settings, the mode does not reduce numerical accuracy. Any agreed differences in compute allocation or support are expressly identified in the Order before acceptance.

3.4 FREE is selected by an affirmative act after the key terms of section 7 have been displayed: either by ticking the initially unticked acceptance box of the Terms of Use, whose label states the FREE data use, or by choosing FREE in the company acceptance dialog. PRIVATE is selected in the company acceptance dialog or by an Order signed with MOTRES. From MOTRES's receipt of a written PRIVATE request at support@aerostator.com until the Order is concluded or declined, MOTRES does not use the requesting company's data for new Internal AI Training. A later switch to FREE follows section 11.3.

3.5 Fees, billing periods, due dates and taxes are as stated in the accepted Order. If an undisputed invoice remains unpaid 30 days after its due date, MOTRES may suspend the Client's access after at least 14 days' written notice. Non-payment, suspension or an invoice dispute does not change data permissions: existing Protected Data remains protected, and a switch to FREE requires the affirmative selection under section 11.3.

3.6 Changes to fees or renewal terms require acceptance under section 12.3. If MOTRES terminates for convenience, or the Client terminates for MOTRES's uncured material breach or after an unresolved justified objection under section 8.3, MOTRES refunds prepaid PRIVATE fees for the unused affected period. Accrued undisputed fees remain due.

## 4. Ownership and intellectual property

4.1 As between the Parties, the Client owns its Client Data and, subject to third-party rights and MOTRES Technology, Results belong to the Client. This allocation applies to the extent rights exist under law; it does not create otherwise unavailable intellectual-property rights.

4.2 MOTRES acquires no ownership of Client Data or Results by hosting or processing them. The Client grants MOTRES a non-exclusive licence to host, process, copy and display them as needed to provide the Platform, support and security, and, in FREE mode only, the licence in section 7.

4.3 MOTRES Technology remains with MOTRES or the relevant rights holders. Software published under the Apache License 2.0 remains available under that licence; this Agreement does not restrict rights granted by it or by another applicable open-source licence.

4.4 No additional patent, manufacturing or know-how exploitation licence is granted by this Agreement or by use of the Platform. Rights under the Apache License 2.0, including its section 3, other applicable licences and mandatory law remain unaffected. Manufacturing rights not already available under those rights require a separate written licence from the relevant rights holder or its authorised licensee; MOTRES offers such licences non-exclusively within its licensing authority. Patent ownership and status are as identified in the applicable licence, and no freedom-to-operate assurance is given. Model Materials are governed by the Model Access NDA.

4.5 Subject to third-party rights, applicable licences and law, the Client may use and exploit its Client Data and Results, including for manufacture. Use of the Platform alone does not subject independently developed Client designs to a MOTRES manufacturing licence. Protected third-party material incorporated in a Result remains subject to the applicable terms identified for it.

4.6 Only material deliberately submitted to a designated public open-source repository as a contribution is governed by that repository's stated contribution and licence terms. The contributor must be authorised and provide any required Developer Certificate of Origin sign-off. Project uploads, support attachments and private communications are not public contributions.

## 5. Confidentiality (both modes)

5.1 All projects are closed by default. MOTRES shall not publish, sell or disclose Client Confidential Information, or give other clients or third parties access to it, except to Authorised Recipients under section 5.6, to service providers under section 8, as required by law under section 5.4, or with the Client's written consent, including a Data Sharing Consent.

5.2 MOTRES personnel access Client Confidential Information only as needed for operation, support, security, compliance or as permitted by this Agreement, and are bound by confidentiality.

5.3 Training authorised under section 7 is not a publication or disclosure and does not authorise disclosure of Client Confidential Information. MOTRES shall apply reasonable controls against memorisation and extraction, investigate reported leakage promptly, and restrict or withdraw affected outputs or models where necessary to meet its confidentiality and legal duties.

5.4 MOTRES may disclose information where required by law or a competent authority; it will notify the Client where legally permitted and disclose only what is required.

5.5 Confidentiality does not apply to information that MOTRES can substantiate as: (a) public without breach; (b) lawfully known to MOTRES without restriction before receipt; (c) independently developed without use of Client Confidential Information; or (d) lawfully obtained without restriction from a third party entitled to disclose it. A combination is not public merely because individual elements are public.

5.6 The Client may share its own Engineering Data with identified Authorised Recipients for specified purposes and access periods, subject to applicable rights, confidentiality obligations and export restrictions. MOTRES implements the permissions the Client selects; the Client is responsible for the recipients it authorises and shall bind them to appropriate confidentiality and restricted-use duties before access. Sharing grants no manufacturing or patent rights. Model Materials and information derived from them remain governed by the Model Access NDA and any applicable manufacturing licence, including any required MOTRES consent.

## 6. PRIVATE mode — no AI training

6.1 In PRIVATE mode MOTRES uses Engineering Data only to perform the requested computations, store projects, operate, secure, support and maintain the Platform, and for inference or review by service providers that meet section 8.2. PRIVATE excludes training, not such inference or review.

6.2 MOTRES shall not use Protected Data, including inputs, intermediate and final results, field data, meshes, geometry, optimisation histories and derived data, for Internal AI Training or for any other AI or machine-learning training, fine-tuning, evaluation or training-dataset collection, and shall not provide it to any third party for such purposes. Protected Data must not enter any such workflow, including automated exporters, optimisation logs, support records and agent traces. Copies kept solely to deliver or secure the service remain subject to this restriction.

6.3 MOTRES records the applicable permission with each project and its derived data, so that section 6.2 follows Protected Data when it is copied, rerun, re-uploaded or combined.

6.4 Aggregated operational statistics (for example run counts, run times, error rates and solver convergence counters) may be used for operational maintenance and security, not for model training or evaluation, and must not allow reconstruction of the Client's engineering information.

6.5 The remedy for a breach of this section 6 is stated in section 10.3.

## 7. FREE mode — internal AI training licence

7.1 Only for Engineering Data lawfully supplied or generated under FREE and not constituting Protected Data, the Client grants MOTRES a non-exclusive, royalty-free licence to conduct Internal AI Training for MOTRES-controlled internal models while those data are lawfully retained and before a cut-off under section 11.1. The licence is limited to the rights the Client can grant and does not override export, personal-data, confidentiality or third-party restrictions. A dataset mixing data of several rights holders is eligible only if each relevant rights holder has expressly authorised that use. Processing by service providers under section 8 grants them no independent training right. Continued operation of models lawfully completed before a cut-off is governed exclusively by section 11.2.

7.2 Eligible data may include, in particular, machine geometry, materials and parameters; FEM meshes and electromagnetic, thermal, mechanical and other simulation results; optimisation data, variants and engineering decisions; validation and convergence data; and test measurements entered by the Client.

7.3 The licence does not permit publishing or selling Engineering Data, giving it to other clients, or licensing third parties to use it. No Personal Data is licensed for AI training; before data enter a training dataset MOTRES removes or excludes Personal Data, including names in file metadata, free-text messages and comments, and assistant or agent conversation records, and excludes data that cannot be reliably cleaned. MOTRES also removes the names of the Client, its customers and suppliers, and prices and quotations.

7.4 Trained models remain under MOTRES's control and are subject to section 5.3.

7.5 MOTRES may use general engineering knowledge, patterns and statistical relationships learned through permitted training, provided it does not disclose Client Confidential Information. MOTRES shall not use the data to appropriate the Client's confidential product designs or to target the Client competitively using project-specific confidential information.

7.6 The Client warrants that it is entitled to grant this licence and to upload the data, including where the data concern its customers', employers' or other rights holders' products or were obtained from third parties, and that the upload and the licence do not breach any third party's rights or any confidentiality, licence or export restriction. Where it cannot give this warranty for particular data, it shall not upload those data in FREE mode. If a rights holder notifies MOTRES in writing that data were uploaded without its authority, MOTRES treats those data as Protected Data from receipt of the notice and applies section 11.1.

7.7 **No further obligations for the training use.** The free service is the entire consideration for the licence in section 7.1. Beyond its duties under sections 5, 7.3 to 7.5, 8 and 11, MOTRES has no obligation to the Client in respect of the training use: no fee, royalty, revenue share or other compensation, no attribution, accounting or reporting, no notice or approval of individual training runs, and no duty to retrain, delete or disclose models lawfully trained before a cut-off. The models and their parameters belong to MOTRES; section 4.1 continues to govern Results generated for the Client.

7.8 **Community dataset.** FREE mode never permits publication of Engineering Data or its inclusion in the engineering dataset shared with the AeroStator community. That requires a Data Sharing Consent, which is optional, separate from this Agreement and withdrawable as stated in it.

7.9 **Data supplied before acceptance.** Acceptance of this Agreement in FREE mode extends the licence in section 7.1 to eligible Engineering Data that the Client or its Users supplied or generated on the Platform before that acceptance. Data of an account or company that has not accepted this Agreement are not used for Internal AI Training. Data supplied in the Client's projects by Authorised Recipients or by Users of another company are eligible only if that contributor has itself accepted FREE mode and the data are not Protected Data.

7.10 **Check at ingestion.** A record enters a training dataset only after an automated check at ingestion confirms that the account or company has a FREE acceptance and is not in PRIVATE mode, that the personal and commercial data listed in section 7.3 have been removed, and that the record is tagged with its account and project so that it can be removed under section 11.1. Records that MOTRES keeps of its own work on a Client's project are not a training dataset unless they pass that check.

## 8. Service providers, security and personal data

8.1 MOTRES may use the service providers listed in Schedule 2 — Service Providers for the stated hosting, communications and AI inference or review purposes. Schedule 2 identifies each provider's contracting entity, service, countries and remote-access locations, retention and purpose. A provider is a subprocessor to the extent it processes Personal Data on MOTRES's behalf where MOTRES is itself a processor; otherwise its data-protection role is specified separately. Provider access is limited to what the stated function requires.

8.2 MOTRES shall bind providers handling project content to confidentiality, security, purpose limits and deletion duties appropriate to that content and to Schedule 1 — Data Processing Addendum. They receive no independent right to train models, build improvement datasets or otherwise reuse project content for their own purposes. Necessary processing licences do not transfer ownership. Any legally required retention or independent processing of administrative metadata is identified in Schedule 2 or the Privacy Notice and creates no permission to train on project content. MOTRES will not submit project content to an AI service whose terms permit the provider to use it for training or to improve its own services. MOTRES remains responsible for its providers' performance of these duties, and for subprocessors as required by law.

8.3 For Personal Data processed under general written subprocessor authorisation, MOTRES gives at least 14 days' advance notice of additions or replacements, with the information needed for a reasoned objection. A justified unresolved objection prevents the proposed provider from receiving the affected data; the Parties shall seek an alternative, failing which either Party may terminate the affected service before that provider begins processing, with the refund stated in section 3.6. Emergency measures remain subject to applicable authorisation and security duties.

8.4 MOTRES applies appropriate technical and organisational measures, described in Schedule 1, including access control, logical separation of clients' projects, encryption in transit and regular backups.

8.5 MOTRES acts as an independent controller for its own account administration, billing, fraud prevention and legal compliance, as specified in the Privacy Notice. For Personal Data processed on the Client's documented instructions in providing project and collaboration functions, MOTRES acts as processor, or as subprocessor where the Client is a processor; Schedule 1 applies to that processing.

8.6 International transfers of Personal Data, including remote access by service providers, are made only with a valid transfer mechanism under applicable law, completed before the affected transfer.

8.7 **Assistants.** The help assistant, the specification assistant and the design assistant are AI systems and say so to the user. They run on provider services that do not use content for training or to improve their own services (Schedule 2).

## 9. Engineering responsibility; no warranty

9.1 Results are numerical models whose accuracy depends on the inputs, models, boundary conditions and settings. MOTRES does not warrant agreement with physical tests.

9.2 Results are engineering aids and do not by themselves constitute certification, regulatory approval or proof of fitness for a safety-critical use. Before manufacture, certification or use in aviation or other safety-critical systems, the Client shall independently verify, test and validate them and satisfy applicable product and certification requirements; the Client remains responsible for its products.

9.3 The Platform and all Results are provided free of charge, "as is" and "as available", without any warranty of accuracy, completeness, fitness for a particular purpose, non-infringement or uninterrupted availability, to the extent permitted by law. Service levels apply only under a separate written agreement.

## 10. Liability

10.1 To the maximum extent permitted by applicable law, MOTRES excludes all liability, whether in contract, in tort (including negligence), under statute or otherwise, arising from or in connection with the Platform, simulations, Results, their use or reliance on them, or this Agreement. This exclusion does not apply to MOTRES's obligations under sections 5, 6, 7.3 to 7.5, 7.10, 8.2, 11.1, 11.5 to 11.7 and Schedule 1 (the "Data Obligations"). For a breach of a Data Obligation by ordinary negligence, MOTRES's total liability is limited to EUR 5,000 or, if higher, the fees the Client paid to MOTRES under this Agreement in the 12 months before the event giving rise to the claim, without affecting the Client's rights to performance, termination, removal of the data and injunctive relief.

10.2 Liability of MOTRES for a product, or for its design, development, supply or manufacture, arises only under a separate written agreement for that project under which the Client pays MOTRES, and only as stated in that agreement. Simulations and Results on the Platform do not create such liability.

10.3 If MOTRES breaches section 6, the Client may, in addition to its rights under section 10.1, terminate the Order with immediate effect and receive a refund of the PRIVATE fees paid for the period affected by the breach; MOTRES shall stop the affected use and remove the affected data under section 11.1.

10.4 Nothing in sections 9 and 10 excludes or limits liability for intentional misconduct or fraud, gross negligence, death or personal injury, or any other liability that cannot be excluded or limited under applicable mandatory law (including, where Slovenian law applies, Article 242 of the Obligations Code), nor any mandatory statutory liability. Nothing in this Agreement restricts a data subject's statutory rights, a regulator's powers, or a court's power to disapply an unfair or disproportionate term. The exclusions apply only to the extent lawful under the governing law and applicable mandatory rules.

## 11. Mode changes, term, termination, deletion and switching

11.1 The Client may switch from FREE to PRIVATE at any time by accepting an Order. A switch to PRIVATE, an effective request to delete the relevant data, or termination ends permission for further Internal AI Training on the affected data immediately at that event (the "cut-off"). On a switch to PRIVATE, the affected data are all the Client's Engineering Data, including data supplied or generated earlier under FREE, which are treated as Protected Data from the cut-off. MOTRES shall stop affected queued or running training and evaluation from further using the data and shall not release a model from an unfinished job relying on them. Within 30 days, without requiring a second request, MOTRES shall remove those data and identifiable training derivatives from active training datasets, staging copies and other training stores, and shall instruct relevant service providers accordingly; until removal they are blocked from training use.

11.2 Deletion of source data does not necessarily remove their influence from models trained before the cut-off. MOTRES may continue operating lawfully completed models only while complying with confidentiality, non-disclosure and applicable law. This does not authorise retention or reuse of source records, retrieval stores or identifiable extracts for new training, or continued use of unlawfully trained or disclosing models. MOTRES does not guarantee reversal of every statistical influence but shall take any corrective action required by law or by its confidentiality obligations.

11.3 A switch to FREE requires an authorised administrator's affirmative selection. It authorises training only on new eligible FREE data. Existing Protected Data and its derivatives remain excluded, including when rerun, copied, optimised, uploaded again or combined with new data. Changing that permission requires separate, specific authorisation identifying the affected data and confirming all necessary rights; selecting FREE alone is insufficient.

11.4 This Agreement starts on acceptance and runs while the Client uses the Platform. Either Party may terminate with 30 days' written notice; either Party may terminate immediately for the other Party's material breach not cured within 15 days of notice. MOTRES may suspend access for security threats, unlawful use or abuse of compute resources, or under section 3.5 or 14.10. Suspension does not change data permissions.

11.5 The Client may export its Client Data and Results during the service and any applicable retrieval period. For an ordinary deletion request or termination unconnected with a statutory switching process, MOTRES deletes the affected data from active service and training systems within 30 days; copies in routine backups, which are not accessible to normal operations, are deleted when the backup rotation stated in Schedule 1 expires, at the latest 12 months after the deletion, unless a different period is required by law. In a switching process, section 11.7 governs retrieval and erasure instead; no deletion will defeat the required retrieval opportunity. Legally required retained copies are isolated, used only for that retention purpose, and protected until deletion. MOTRES instructs relevant providers to delete corresponding copies, records completion or applicable lawful exceptions, and reapplies deletion restrictions before a backup is restored.

11.6 Export of Client Data and Results, and switching to another provider or to the Client's own infrastructure, are free of charge: MOTRES charges no switching, export or data-egress fee. Exportable data and digital assets, supported formats, interfaces, procedures, restrictions and any exhaustively identified excluded categories are listed in Schedule 3 — Export Register; exclusions must not obstruct switching.

11.7 Where Chapter VI of the EU Data Act applies, the Client may also switch to another provider or its own infrastructure, or request erasure, by written notice. The notice period is 30 calendar days. MOTRES will support the transfer within a further 30 calendar days, maintain contractual continuity and security, provide reasonable migration assistance, identify known continuity risks and cooperate in good faith. Any legally permitted extension requires timely written justification and remains within statutory limits; the Client may extend the transition as the law permits. The service terminates on successful completion of switching, notified to the Client, or after the notice period for an erasure-only request. Data remain retrievable for at least 30 calendar days after the transition. After successful switching and expiry of the retrieval period, MOTRES fully erases the exportable data and digital assets from active systems and removes them from backups by rewriting the affected backup snapshots, unless a later date is expressly agreed or retention is legally required; the backup rotation in section 11.5 does not postpone an earlier mandatory erasure. Schedule 3 also identifies infrastructure jurisdictions and safeguards against conflicting foreign-government access. Agreed PRIVATE fees remain payable only for service supplied during the applicable periods; no early-termination charge applies.

11.8 Accrued rights and payment and refund obligations survive termination. Sections 1, 4–6, 7.3–7.8, 7.10, 8–10, 11.1–11.7 and 12–15 and the Schedules survive only as necessary for their stated purpose, including retained data, models, records and disputes. Sections 7.1 and 7.9 do not survive their cut-off. Confidentiality of Client Confidential Information lasts five years after termination; retained copies remain confidential until deletion, and trade secrets remain protected while they qualify as trade secrets. Nothing shortens the separate duration of the Model Access NDA.

## 12. General

12.1 Each Party complies with export-control and trade restrictions applicable to its activities. Before providing potentially controlled technical data or requesting a controlled service, the Client must obtain MOTRES's written acceptance of a documented handling plan and all legally required authorisations. Mere notification or Platform acceptance does not authorise a restricted transaction. The plan addresses classification, end use, end users, countries, remote access, service providers and onward sharing. MOTRES shall not enable prohibited processing or disclosure and shall identify controls known to it before releasing its own controlled material. No provision requires either Party to act unlawfully.

12.2 A separate written agreement between the Parties (for example for development, supply, a manufacturing licence or a production project) governs its own subject matter, including liability under section 10.2; it displaces provisions of this Agreement only to the extent it expressly identifies them. The Model Access NDA governs Model Materials and restricted information derived from them, including permitted use, recipients, duration and disputes; this Agreement governs the Client's own project data and the Platform service. For mixed material each agreement's obligations continue to the extent compatible, and no Platform permission relaxes a restriction on Model Materials. The Terms of Use govern the account; where they and this Agreement differ on Engineering Data, AI training, confidentiality or liability, this Agreement prevails.

12.3 MOTRES may publish a new version. It takes effect prospectively, and only on the Client's separate affirmative acceptance; until then the accepted version continues to apply. A new version does not expand training permissions over previously supplied or generated data, change earlier supplies of Model Materials or extinguish accrued obligations unless those changes are separately and expressly agreed.

12.4 The English text governs, subject to mandatory law. The Chinese text is a full mirror made available before acceptance.

12.5 Notices go to the Parties' designated notice addresses displayed and retained at acceptance, or to replacement addresses notified in writing. The sender retains evidence of receipt and uses another effective means if delivery fails. This section does not replace mandatory rules on service of proceedings.

12.6 An invalid provision is severed only to the extent of its invalidity; the rest of this Agreement continues where legally possible.

## 13. Governing law and disputes

{{#unless country_is_cn_hk}}

*Variant A — Slovenian law and the courts of Ljubljana.*

13.1 This Agreement is governed by Slovenian substantive law, excluding its conflict-of-law rules and the UN Convention on Contracts for the International Sale of Goods. Mandatory laws remain applicable where required.

13.2 Subject to mandatory jurisdiction rules, the courts of Ljubljana, Slovenia, have exclusive jurisdiction over disputes arising from or connected with this Agreement.

13.3 Either Party may seek interim protective measures from another competent court without changing the agreed forum for the merits.

{{/unless}}

{{#if country_is_cn_hk}}

*Variant B — Hong Kong law and HKIAC arbitration.*

13.1 This Agreement and its arbitration agreement are governed by Hong Kong SAR law, excluding its conflict-of-law rules. Mandatory laws remain applicable where required.

13.2 Any dispute arising from or connected with this Agreement, including its existence, validity, interpretation, performance, breach, termination or related non-contractual obligations, shall be finally resolved by arbitration administered by the Hong Kong International Arbitration Centre (HKIAC) under its Administered Arbitration Rules in force when the Notice of Arbitration is submitted. The seat is Hong Kong, there is one arbitrator, and the arbitration language is English.

13.3 Either Party may seek interim or preservation measures from a competent court or the arbitral tribunal, including through the applicable Mainland–Hong Kong interim-measures arrangement where its requirements are met. Such application does not waive arbitration. The award binds the Parties, subject to statutory review and enforcement procedures, including the applicable Mainland–Hong Kong award-enforcement arrangement. This section does not confer jurisdiction or remedies a court does not possess.

{{/if}}

13.4 The regional annexes in section 14 do not select an additional forum. A project involving several countries remains subject to the single forum selected in this section. For an account acceptance under section 2.3, the account holder states at acceptance the country in which the business named under section 2.3, or its own business, is established; Variant B applies if that is Mainland China or the Hong Kong SAR, otherwise Variant A. The text displayed for an account acceptance contains both variants, and the acceptance record identifies the applicable one.

## 14. Regional annexes

14.1 A regional data or trade annex applies when the relevant law applies to the processing, persons, goods, technology or transaction, irrespective of the Client's place of incorporation. Mandatory law and mandatory transfer terms prevail; otherwise an annex prevails only on its stated subject. Forum selection follows section 13 and is not changed by an annex.

14.2 **EU/EEA.** For processing subject to the GDPR, Schedule 1 applies to processing on instructions. Restricted transfers use an applicable adequacy decision or completed lawful safeguards with the required assessments and supplementary measures. Mandatory EU data-access, switching and unfair-terms rights remain unaffected; section 11.7 applies where required. Each Party shall meet the AI Act duties applicable to its actual role and the system's intended purpose. MOTRES shall document classification, authorised training-data provenance, applicable transparency and AI-literacy measures, and required safeguards. This Agreement grants no exemption from data-protection, intellectual-property or product-safety law and does not certify a Client product.

14.3 **United Kingdom.** Where UK data-protection law applies, Schedule 1 incorporates the required UK terms. Restricted transfers use applicable UK adequacy regulations, or a completed UK International Data Transfer Agreement or EU Standard Contractual Clauses with the UK Addendum, supported by the required assessment. The EU Data Act and AI Act apply only where their own scope so provides.

14.4 **Switzerland.** Where the Swiss Federal Act on Data Protection applies, Schedule 1 also governs processing on instructions under its Article 9. Cross-border disclosure complies with its Articles 16–17, using recognised adequacy or safeguards, including necessary Swiss adaptations to standard clauses. EU law applies only where independently applicable.

14.5 **Mainland China.** The Platform is hosted outside Mainland China, as stated in Schedule 2. For Mainland-origin or otherwise PRC-regulated data, each Party shall perform its applicable duties under the Personal Information Protection Law, the Data Security Law and the data-export rules. Before outbound processing, the Client identifies any personal, sensitive personal, important or restricted data and completes any required assessment, standard contract, certification, notice or consent, taking account of lawful exemptions. MOTRES supplies reasonably necessary recipient information and complies with its own applicable recipient and onward-transfer duties. Data whose export is prohibited, or requires approval that has not been obtained, must not be uploaded. Approved destinations, purposes, recipients and retention are recorded before transfer.

14.6 **Hong Kong SAR.** Where the Personal Data (Privacy) Ordinance applies, each Party shall meet its applicable data-use, security, retention and processor-management duties. Overseas handling is subject to applicable transfer requirements and contractual safeguards. Mainland China rules apply only where independently triggered by the data or processing.

14.7 **Macau SAR.** Where Macau Law 8/2005 applies, processing and transfers outside Macau must satisfy its requirements, including Articles 19–20 and any required notification or authorisation. Each Party cooperates in the applicable exporter and recipient duties. Mainland China rules are not applied solely because the Client is incorporated in Macau.

14.8 **United States.** No ITAR technical data or defence service, or other export-restricted technology requiring special handling, may be submitted without MOTRES's prior written acceptance of a compliant handling plan and all required governmental authorisations. EAR classification is recorded by ECCN or as EAR99, as applicable; EAR99 is not an exemption from end-use, end-user, destination or sanctions controls. Each output and onward transfer requires its own classification and authorisation assessment. These controls apply whenever legally relevant, irrespective of the Client's nationality.

14.9 **Other jurisdictions.** Where the following laws apply, the Parties shall, before the affected processing or transfer:
(a) **Japan:** meet the applicable outsourcing, security and overseas-transfer requirements of the APPI, including Article 28, and establish the relevant recognised-country, equivalent-protection or consent basis, with the required information and oversight, before a restricted onward transfer;
(b) **Republic of Korea:** meet Article 28-8 of the PIPA and related disclosure and protective-measure requirements for overseas processing, access and onward transfers, and establish the lawful transfer basis and recipient details;
(c) **India:** perform their fiduciary or processor duties under applicable Indian data-protection law and comply with operative transfer restrictions and sectoral requirements; DPDP Act obligations apply as the relevant provisions commence, and uncommenced provisions are not treated as operative;
(d) **Türkiye:** use an Article 9 route under Law 6698 with the necessary safeguards; if a Turkish standard contract is used, the responsible Party makes the required notification within five business days of signature, and any further transfer meets the applicable requirements;
(e) **United Arab Emirates:** identify whether the federal PDPL, DIFC or ADGM regime applies to the affected processing, one not automatically satisfying the others, and satisfy the required overseas-transfer safeguards and sectoral restrictions.

14.10 **Sanctions.** MOTRES will not provide services or make funds or economic resources available where prohibited by applicable sanctions, including relevant listing, ownership or control, sectoral, destination and anti-circumvention rules. This includes restrictions under Regulation (EU) No 833/2014, including Article 5n and covered design and manufacturing software, and the separate applicable Belarus regime under Regulation (EC) No 765/2006. UK, US and UN-related restrictions apply to the extent legally applicable to the relevant Party or transaction. The Client shall provide reasonably requested end-user and ownership information and shall not facilitate prohibited indirect access through affiliates, factories or intermediaries. MOTRES may suspend affected access where necessary to comply with law; any available exemption or authorisation must be verified before service proceeds.

## 15. Acceptance

15.1 Before a company acceptance, MOTRES presents the completed English and Chinese texts, the selected section 13 variant, the mode, any Order and the Schedules in a form the Client can retain. An authorised representative accepts for the Client by deliberately ticking an initially unticked acceptance box and entering their full name, intending an electronic signature binding the Client, including section 13. No qualified-signature status is asserted.

15.2 An account acceptance under section 2.3 is made by stating the country of establishment, optionally the business name, and ticking the initially unticked acceptance box of the Terms of Use, which links this Agreement, the Schedules and the Privacy Notice and states the exchange of free simulation for the FREE data use, the use of earlier data and that trained models are kept. The selection of FREE under section 3.4 and any authorisation under section 11.3 are recorded affirmative choices.

15.3 MOTRES retains the rendered texts or their SHA-256 hashes, the version and Schedule identifiers, the selected section 13 variant and mode, the UTC time, the account, any company and signatory details, the IP address and evidence of affirmative assent; it makes the accepted texts available for download before and after acceptance, provides a reproducible copy to the Client on request, and for a company acceptance a downloadable PDF. Records are rebuttable evidence, not conclusive proof of identity or authority. Retention and personal-data processing follow the Privacy Notice.

MOTRES d.o.o. — offer published on the Platform under this version.

{{company}} — {{signatory}}, {{title}}, {{date}}

*Acceptance record: {{company}} / {{signatory}} / {{date}} / {{version}} / {{mode}}. The selected section 13 variant and both rendered language texts form part of the retained record.*
