# DRAFT — NOT FOR SIGNATURE

# AeroStator Platform Services and Data Agreement

DRAFT v0.1 — 27 October 2026 — NOT FOR SIGNATURE (owner review, then independent review, then counsel)

**Parties.** MOTRES d.o.o., registration no. 7255691000, Kotnikova 34, 1000 Ljubljana, Slovenia ("MOTRES"); and **{{company}}**, registration no. {{reg_no}}, incorporated in {{country}}, registered address {{address}} (the "Client"), represented by {{signatory}}, {{title}}, e-mail {{email}}. Accepted online on {{date}}. Selected mode: **{{mode}}** (FREE or PRIVATE).

**Important terms:** business use only; your projects stay yours and stay confidential in both modes; in FREE mode MOTRES may use your engineering data to train its own internal AI models, in PRIVATE mode it may not; simulation results must be verified before manufacture or use in safety-critical systems; liability is limited in section 10; the court or arbitration clause is in section 13.

## 1. Definitions

1.1 **Platform**: the AeroStator engineering platform at aerostator.com and its interfaces (web, API, agent tools), operated by MOTRES.

1.2 **Client Data**: everything the Client or its Users upload or enter into the Platform — geometry, CAD models, materials, winding schemes, requirements, specifications, test measurements, documents and settings.

1.3 **Results**: outputs generated for the Client's projects — FEM and other simulation results, field data, meshes, optimisation histories, design variants, reports, charts and derived engineering data.

1.4 **Engineering Data**: Client Data and Results, excluding Personal Data.

1.5 **Personal Data**: information relating to an identified or identifiable natural person, such as names, e-mail addresses and login records of Users.

1.6 **Users**: individuals the Client authorises to use the Platform on its behalf.

1.7 **Authorised Recipients**: persons the Client admits to its project or production through the Platform's sharing functions or names in writing — for example its engineers, collaborators, manufacturers and OEM producers.

1.8 **MOTRES Technology**: the Platform, its software, models, methods, algorithms, material and component libraries, catalogue motors, AeroStator Core motor technology, patents, know-how and documentation, including improvements of them.

1.9 **Internal AI Training**: using data to train, fine-tune, evaluate or build datasets for AI or machine-learning models that MOTRES controls.

## 2. Scope and business use

2.1 MOTRES provides access to the Platform for designing, simulating, optimising, testing and producing electric machines and related components (controllers, propellers, drives and similar).

2.2 The Platform is offered to businesses only. The signatory confirms that the Client is a company or registered entrepreneur acting for business purposes, that the details given are correct and that the signatory has authority to bind the Client. It is not offered to consumers. MOTRES may require corporate verification before granting access or specific functions.

2.3 The Client is responsible for its Users and their compliance with this Agreement, and keeps their access credentials secure.

## 3. Modes

3.1 **FREE**: no fee. Projects are confidential (section 5) and MOTRES may use Engineering Data for Internal AI Training under section 7.

3.2 **PRIVATE**: fee as stated in the order or price list shown before the mode is selected. Projects are confidential (section 5) and Internal AI Training on the Client's Engineering Data is excluded under section 6.

3.3 Both modes use the same solvers, algorithms and accuracy. Compute queues, quotas and support levels may differ only as stated in the order or price list.

3.4 Fees are payable in advance as invoiced, exclusive of VAT and other taxes. If an invoice is unpaid 30 days after its due date, MOTRES may, after written notice, switch the Client to FREE for new data; data created while PRIVATE remains protected by section 6.

## 4. Ownership

4.1 As between the Parties, the Client owns its Client Data. Subject to third-party rights and MOTRES Technology, Results belong to the Client.

4.2 MOTRES acquires no ownership of Client Data or Results by hosting or processing them. The Client grants MOTRES a non-exclusive licence to host, process, copy and display them as needed to provide the Platform, support and security, and in FREE mode the licence in section 7.

4.3 MOTRES Technology remains with MOTRES and its licensors. The Platform software published under the Apache License 2.0 remains available under that licence; this Agreement does not restrict rights granted by it or by another applicable open-source licence.

4.4 No patent licence and no right to manufacture products using MOTRES patents, AeroStator Core technology or catalogue motor designs is granted by this Agreement or by use of the Platform. Manufacturing licences are non-exclusive and granted only by separate written agreement. Access to downloadable models (e.g. STEP) is governed by the AeroStator Core Model Access and Non-Disclosure Agreement.

4.5 Designs the Client develops independently, without using MOTRES Technology or third-party rights, may be used by the Client freely.

4.6 Code, modules or other contributions the Client submits to MOTRES's open-source repositories are licensed under the Apache License 2.0 with the Developer Certificate of Origin and are not Client Data.

## 5. Confidentiality (both modes)

5.1 All projects are closed by default. MOTRES shall not publish, sell, disclose or give access to Engineering Data to other clients or third parties, except to Authorised Recipients, to subprocessors under section 8, as required by law under 5.4, or with the Client's written consent.

5.2 MOTRES personnel access Engineering Data only as needed for operation, support, security, compliance or as permitted by this Agreement, and are bound by confidentiality.

5.3 Use for Internal AI Training in FREE mode is not a publication or disclosure; trained models are not made available in a way that lets third parties obtain Engineering Data.

5.4 MOTRES may disclose information where required by law or a competent authority; it will notify the Client where legally permitted and disclose only what is required.

5.5 Confidentiality does not apply to information that is or becomes public without breach, was lawfully known to MOTRES before receipt, is independently developed without use of Engineering Data, or is lawfully received from a third party without a duty of confidence.

## 6. PRIVATE mode — no AI training

6.1 In PRIVATE mode MOTRES uses Engineering Data only to perform the requested computations, store projects, operate, secure, support and maintain the Platform.

6.2 MOTRES shall not use the Client's Engineering Data — inputs, intermediate and final results, field data, meshes, geometry, optimisation histories or derived data — for Internal AI Training, and shall not provide it to any third party for training its models.

6.3 Aggregated operational statistics (e.g. run counts, run times, error rates, solver convergence counters) may be used only for operating, securing and improving the service, and must not reproduce or allow reconstruction of the Client's engineering information.

## 7. FREE mode — internal AI training licence

7.1 In FREE mode the Client grants MOTRES a non-exclusive, royalty-free, worldwide licence, for the duration of the applicable data's retention and the life of models trained on it, to use Engineering Data for Internal AI Training and for testing and improving MOTRES's models and Platform.

7.2 The licence covers in particular machine geometry, materials and parameters; FEM meshes and electromagnetic, thermal, mechanical and other simulation results; optimisation data, variants and engineering decisions; validation and convergence data; and test measurements entered by the Client.

7.3 The licence does not permit publishing or selling Engineering Data, giving it to other clients, or licensing third parties to use it. Personal Data is excluded from Internal AI Training.

7.4 Trained models remain under MOTRES's control. MOTRES shall take reasonable technical measures so that its models do not reproduce the Client's confidential Engineering Data in outputs to others.

7.5 MOTRES may use general engineering knowledge, patterns and statistical relationships learned through permitted training, provided it does not disclose the Client's confidential information.

7.6 The Client warrants that it is entitled to grant this licence, including where the data concerns its customers', employers' or other rights holders' products.

## 8. Subprocessors, security and personal data

8.1 MOTRES uses subprocessors for hosting, e-mail, storage and AI-assisted processing (for example analysis or review by AI services). The current list is published at {{subprocessor_url}}; MOTRES gives at least 14 days' notice of additions, and the Client may terminate if it reasonably objects.

8.2 Subprocessors are bound by written terms with confidentiality and security obligations at least equivalent to this Agreement, process data only for MOTRES's instructed purposes, and acquire no rights of their own. AI-service subprocessors must be engaged under terms that prohibit training their models on the data submitted.

8.3 MOTRES applies appropriate technical and organisational measures, including access control, logical separation of clients' projects, encrypted transport and backups.

8.4 Each Party processes Personal Data in accordance with applicable law, including the GDPR where it applies. MOTRES processes User Personal Data as controller for account, security and service purposes as described in its privacy notice; where it processes Personal Data on the Client's behalf, the Parties' data-processing addendum applies.

8.5 International transfers of Personal Data are made only with a valid transfer mechanism under applicable law.

## 9. Engineering responsibility

9.1 Results are numerical models whose accuracy depends on the inputs, models, boundary conditions and settings. MOTRES does not warrant agreement with physical tests under all conditions.

9.2 Results are not certification evidence. Before manufacture, certification or use in aviation or other safety-critical systems, the Client shall perform its own engineering verification, testing and validation and remains responsible for its products.

9.3 The Platform is provided as available. Service levels apply only if stated in an order.

## 10. Liability

10.1 Neither Party is liable for indirect or consequential loss, lost profit, lost production or loss of data beyond the cost of restoring it from MOTRES's backups.

10.2 MOTRES's total liability arising from or in connection with this Agreement in any 12-month period is limited to the fees paid by the Client for PRIVATE mode in the preceding 12 months, and in FREE mode to EUR {{free_cap}}.

10.3 Sections 10.1 and 10.2 do not limit liability for intent or gross negligence, for death or personal injury, or any other liability that cannot be limited under applicable law.

## 11. Mode changes, term, termination and deletion

11.1 The Client may switch from FREE to PRIVATE at any time through the Platform. From the switch, no new Internal AI Training on its Engineering Data takes place; at the Client's request MOTRES removes the Client's Engineering Data from active training datasets within 30 days.

11.2 Training already completed cannot be undone in model weights; MOTRES does not guarantee reversal of completed training. Section 7.4 continues to apply to such models.

11.3 A switch from PRIVATE to FREE applies to Engineering Data created after the switch unless the Client expressly consents for earlier data.

11.4 This Agreement runs while the Client uses the Platform. Either Party may terminate with 30 days' written notice; either Party may terminate immediately for material breach not cured within 15 days of notice. MOTRES may suspend access for security threats, unlawful use or abuse of compute resources.

11.5 The Client may export its Client Data and Results. Within 30 days of a valid deletion request or termination, MOTRES deletes them from active systems; backups are overwritten in the normal rotation within 90 days. Data that law requires to be kept is retained only for that purpose.

11.6 Sections 4, 5, 7.3–7.5, 9, 10, 11.2, 11.5, 12 and 13 survive termination. Confidentiality lasts five years after termination and, for trade secrets, as long as they remain trade secrets.

## 12. General

12.1 Each Party complies with export-control, sanctions and trade laws applicable to its acts. The Client shall not upload export-controlled technical data without telling MOTRES first, and shall not use the Platform or Results contrary to applicable restrictions.

12.2 A separate written agreement between the Parties covering the same subject (e.g. development, supply, manufacturing licence, NDA) prevails over conflicting terms of this Agreement for its subject matter.

12.3 MOTRES may publish a new version. It applies to the Client only after the Client's acceptance; until then the accepted version continues to apply.

12.4 The English text governs. The Chinese text is a translation provided before acceptance.

12.5 Notices are given by e-mail to the addresses in the acceptance record.

## 13. Governing law and disputes

**Variant A — default.**

13.1 This Agreement is governed by Slovenian law, excluding its conflict-of-law rules and the UN Convention on Contracts for the International Sale of Goods.

13.2 Subject to mandatory jurisdiction rules, the courts of Ljubljana, Slovenia, have exclusive jurisdiction. Either Party may seek interim protective measures from another competent court.

**Variant B — Client incorporated in Mainland China, Hong Kong SAR or Macau SAR.**

13.1 This Agreement and its arbitration agreement are governed by Hong Kong SAR law, excluding its conflict-of-law rules.

13.2 Any dispute arising from or connected with this Agreement shall be finally resolved by arbitration administered by the Hong Kong International Arbitration Centre under its Administered Arbitration Rules in force when the Notice of Arbitration is submitted. The seat is Hong Kong, there is one arbitrator, and the language is English. Either Party may seek interim or preservation measures from a competent court or the tribunal.

## 14. Regional annexes

14.1 The annex for the Client's country of incorporation forms part of this Agreement and prevails over the main text for its subject. Where the Client processes data from several regions, each applicable annex applies to that data.

**Annex EU/EEA (and Switzerland).** The data-processing addendum under Art. 28 GDPR applies where MOTRES processes Personal Data on the Client's behalf. Transfers of Personal Data outside the EEA use an adequacy decision or the EU Standard Contractual Clauses. The Client's rights to access and port data generated by its use of the Platform under applicable EU data law (including the Data Act where it applies) are not restricted by this Agreement. MOTRES's use of Engineering Data for Internal AI Training in FREE mode is documented and its trained models are operated in compliance with the EU AI Act where applicable.

**Annex United Kingdom.** As the EU annex, with the UK GDPR and the UK International Data Transfer Addendum in place of the EU instruments.

**Annex Mainland China, Hong Kong SAR, Macau SAR.** Section 13 Variant B applies. The Platform is hosted outside Mainland China; uploading data to it is an outbound transfer. The Client is responsible for complying with the Personal Information Protection Law, the Data Security Law and the outbound data-transfer rules of the PRC (including any security assessment, standard contract or certification and the identification of "important data") before uploading, and shall not upload data whose export is prohibited or not yet approved. MOTRES provides the information the Client reasonably needs for such filings.

**Annex United States.** The Client shall not upload technical data controlled under the International Traffic in Arms Regulations (ITAR) or classified above EAR99 under the Export Administration Regulations without MOTRES's prior written agreement on handling. Results remain subject to the export classification of the underlying data.

**Annex sanctions.** The Platform is not provided to persons listed under, or owned or controlled by persons listed under, EU, UN, UK or US sanctions, nor where EU restrictive measures prohibit the service (including the restrictions on engineering, IT and software services to entities established in Russia or Belarus). The Client confirms it is not such a person and will not give access to such persons. MOTRES may suspend access immediately where required by sanctions law.

## 15. Acceptance

The Client accepts this Agreement online by ticking the acceptance box and typing the signatory's name. MOTRES keeps an acceptance record with the version, the text hash, the selected mode, the time and the IP address.

MOTRES d.o.o. — {{motres_signatory}}

{{company}} — {{signatory}}, {{title}}, {{date}}
