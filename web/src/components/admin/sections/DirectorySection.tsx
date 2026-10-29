/**
 * Admin · Companies & People — catalog of companies, people, their affiliations,
 * commercial documents and audit trails.
 *
 * Contract: BRIEF_companies_people_catalog.md
 * - Companies table with details drawer (people, documents, audit trail, productions)
 * - People table with affiliations
 * - Affiliations management (confirm, end, invite)
 * - Proposed affiliations review badge and queue
 * - Strict bank field prohibition
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Box, Typography, Paper, Chip, Button, CircularProgress,
  Table, TableBody, TableCell, TableHead, TableRow, TextField, Dialog,
  DialogTitle, DialogContent, DialogActions, IconButton,
  Drawer, Select, MenuItem, FormControl, InputLabel, Alert,
} from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import AddIcon from '@mui/icons-material/Add';
import CheckCircleOutlineIcon from '@mui/icons-material/CheckCircleOutline';
import CancelOutlinedIcon from '@mui/icons-material/CancelOutlined';
import SyncIcon from '@mui/icons-material/Sync';
import CloseIcon from '@mui/icons-material/Close';
import { useTranslation } from 'react-i18next';
import { getStoredToken } from '../../../lib/localAuth';
import { oneOf } from '../../../lib/viewMemory';
import { useRememberedView } from '../../../lib/useRememberedView';
import SubTabs from '../../common/SubTabs';
import { companyAgreementText } from '../../../lib/platformAgreement';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001').replace(/\/$/, '');
const PANEL = { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5 } as const;

interface DocumentLine {
  description: string;
  qty: number;
  unit_price: number;
  amount: number;
}

interface CommercialDocument {
  id: string;
  number: string;
  date: string;
  kind: 'proforma' | 'invoice' | 'PO';
  currency: string;
  total: number;
  lines: DocumentLine[];
  terms: string;
  status: 'open' | 'paid' | 'cancelled';
  paid_on?: string | null;
}

interface AffiliationInfo {
  id: string;
  title: string;
  status: 'proposed' | 'confirmed' | 'ended';
  from_date: string;
  to_date: string;
  confirmed_by?: string;
  source?: string;
}

interface Person {
  id: string;
  name: string;
  emails: string[];
  user_id?: string;
  phone?: string;
  notes?: string;
  source?: string;
  created?: string;
  affiliation?: AffiliationInfo;
  companies?: {
    affiliation_id: string;
    company_id: string;
    legal_name: string;
    title: string;
    status: 'proposed' | 'confirmed' | 'ended';
    from_date: string;
    to_date: string;
  }[];
}

interface ProductionLink {
  die: string;
  config: string;
  title: string;
  matched_members: string[];
  members_count: number;
}

interface AuditEntry {
  ts: number;
  iso: string;
  actor: string;
  action: string;
  target_type: string;
  target_id: string;
  details: Record<string, unknown>;
}

interface Company {
  id: string;
  legal_name: string;
  local_name?: string;
  country: string;
  address?: string;
  phone?: string;
  website?: string;
  reg_no?: string;
  vat_no?: string;
  roles: string[];
  notes?: string;
  domains: string[];
  nda: { status: string; date?: string; record_id?: string; version?: string };
  verification: { status: string; case_id?: string };
  /** Platform Agreement of the company (motor_ai_sim/platform_agreement.py): the mode and version in force, or null. */
  agreement?: { mode: string; version: string; iso?: string } | null;
  documents: CommercialDocument[];
  source: string;
  created: string;
  updated: string;
  people_count?: number;
  proposed_count?: number;
  documents_summary?: {
    total_count: number;
    open_count: number;
    paid_count: number;
    cancelled_count: number;
    open_total: number;
    paid_total: number;
    currency: string;
  };
  people?: Person[];
  audit_trail?: AuditEntry[];
  productions?: ProductionLink[];
}

interface ProposedAffiliation {
  id: string;
  company_id: string;
  person_id: string;
  company_name: string;
  company_country: string;
  person_name: string;
  person_emails: string[];
  person_user_id: string;
  title: string;
  from_date: string;
  status: string;
  source: string;
  created: string;
}

const authHeaders = (): HeadersInit => {
  const t = getStoredToken();
  const h: Record<string, string> = { 'Content-Type': 'application/json' };
  if (t) {
    h['Authorization'] = `Bearer ${t}`;
  }
  return h;
};

const DirectorySection: React.FC = () => {
  const { t } = useTranslation('admin');
  const [tab, setTab] = useRememberedView<'companies' | 'people' | 'proposed'>('admin.directory.tab', 'companies', oneOf(['companies', 'people', 'proposed'] as const));
  const [companies, setCompanies] = useState<Company[]>([]);
  const [people, setPeople] = useState<Person[]>([]);
  const [proposed, setProposed] = useState<ProposedAffiliation[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  // Detail Drawer state
  const [selectedCompanyId, setSelectedCompanyId] = useState<string | null>(null);
  const [detailCompany, setDetailCompany] = useState<Company | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  // Search & Filter
  const [search, setSearch] = useState('');

  // Dialogs
  const [companyModalOpen, setCompanyModalOpen] = useState(false);
  const [editingCompany, setEditingCompany] = useState<Partial<Company> | null>(null);

  const [inviteModalOpen, setInviteModalOpen] = useState(false);
  const [inviteCompanyId, setInviteCompanyId] = useState<string>('');
  const [inviteForm, setInviteForm] = useState({ email: '', name: '', title: '' });

  const [docModalOpen, setDocModalOpen] = useState(false);
  const [docForm, setDocForm] = useState<{
    number: string;
    date: string;
    kind: 'invoice' | 'proforma' | 'PO';
    currency: string;
    terms: string;
    status: 'open' | 'paid' | 'cancelled';
    lines: { description: string; qty: number; unit_price: number }[];
  }>({
    number: '',
    date: new Date().toISOString().slice(0, 10),
    kind: 'invoice',
    currency: 'EUR',
    terms: 'Net 30',
    status: 'open',
    lines: [{ description: '', qty: 1, unit_price: 0 }],
  });

  // The Platform Agreement text is shipped as a draft and enforced only once an approved version is installed: tell the admin which.
  const [agreementInstalled, setAgreementInstalled] = useState<boolean | null>(null);
  useEffect(() => {
    let live = true;
    fetch(`${API}/api/agreement/status`, { headers: authHeaders() }).then((r) => (r.ok ? r.json() : null))
      .then((j: { admin?: { installed?: boolean } } | null) => { if (live && j?.admin) setAgreementInstalled(j.admin.installed === true); })
      .catch(() => { /* the note just stays hidden */ });
    return () => { live = false; };
  }, []);

  // Fetch all data
  const loadAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const headers = authHeaders();
      const [cRes, pRes, propRes] = await Promise.all([
        fetch(`${API}/api/admin/directory/companies`, { headers }).then((r) => {
          if (!r.ok) throw new Error(`Companies HTTP ${r.status}`);
          return r.json();
        }),
        fetch(`${API}/api/admin/directory/people`, { headers }).then((r) => {
          if (!r.ok) throw new Error(`People HTTP ${r.status}`);
          return r.json();
        }),
        fetch(`${API}/api/admin/directory/affiliations/proposed`, { headers }).then((r) => {
          if (!r.ok) throw new Error(`Proposed HTTP ${r.status}`);
          return r.json();
        }),
      ]);
      setCompanies(cRes.companies || []);
      setPeople(pRes.people || []);
      setProposed(propRes.proposed || []);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  // Load single company detail
  const loadCompanyDetail = useCallback(async (id: string) => {
    setDetailLoading(true);
    try {
      const headers = authHeaders();
      const r = await fetch(`${API}/api/admin/directory/companies/${encodeURIComponent(id)}`, { headers });
      if (r.ok) {
        const j = await r.json();
        setDetailCompany(j);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setDetailLoading(false);
    }
  }, []);

  const openCompanyDetail = (id: string) => {
    setSelectedCompanyId(id);
    void loadCompanyDetail(id);
  };

  const closeCompanyDetail = () => {
    setSelectedCompanyId(null);
    setDetailCompany(null);
  };

  // Backfill NDA
  const runBackfill = async () => {
    setLoading(true);
    try {
      const headers = authHeaders();
      const r = await fetch(`${API}/api/admin/directory/backfill/nda`, { method: 'POST', headers });
      if (r.ok) {
        const res = await r.json();
        setSuccessMsg(
          `NDA backfill complete: ${res.processed_records} records processed, ${res.companies_created} companies created.`
        );
        void loadAll();
      } else {
        throw new Error(`Backfill failed HTTP ${r.status}`);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  // Confirm affiliation
  const handleConfirmAffiliation = async (affId: string) => {
    try {
      const headers = authHeaders();
      const r = await fetch(`${API}/api/admin/directory/affiliations/${encodeURIComponent(affId)}/confirm`, {
        method: 'POST',
        headers,
      });
      if (r.ok) {
        setSuccessMsg(t('directory.affiliationConfirmed', { defaultValue: 'Affiliation confirmed' }));
        void loadAll();
        if (selectedCompanyId) void loadCompanyDetail(selectedCompanyId);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  // End affiliation
  const handleEndAffiliation = async (affId: string) => {
    try {
      const headers = authHeaders();
      const r = await fetch(`${API}/api/admin/directory/affiliations/${encodeURIComponent(affId)}/end`, {
        method: 'POST',
        headers,
      });
      if (r.ok) {
        setSuccessMsg(t('directory.affiliationEnded', { defaultValue: 'Affiliation ended' }));
        void loadAll();
        if (selectedCompanyId) void loadCompanyDetail(selectedCompanyId);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  // Save company (Create / Update)
  const saveCompany = async () => {
    if (!editingCompany?.legal_name?.trim()) {
      setError('Legal name is required');
      return;
    }
    try {
      const headers = authHeaders();
      const isEdit = Boolean(editingCompany.id);
      const url = isEdit
        ? `${API}/api/admin/directory/companies/${encodeURIComponent(editingCompany.id!)}`
        : `${API}/api/admin/directory/companies`;
      const method = isEdit ? 'PATCH' : 'POST';

      const r = await fetch(url, {
        method,
        headers,
        body: JSON.stringify(editingCompany),
      });

      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(j.detail || `HTTP ${r.status}`);
      }

      setCompanyModalOpen(false);
      setEditingCompany(null);
      void loadAll();
      if (editingCompany.id) void loadCompanyDetail(editingCompany.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  // Invite Person to Company
  const submitInvite = async () => {
    if (!inviteForm.email.trim()) return;
    try {
      const headers = authHeaders();
      const r = await fetch(`${API}/api/admin/directory/companies/${encodeURIComponent(inviteCompanyId)}/invite`, {
        method: 'POST',
        headers,
        body: JSON.stringify(inviteForm),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(j.detail || `HTTP ${r.status}`);
      }
      setInviteModalOpen(false);
      setInviteForm({ email: '', name: '', title: '' });
      setSuccessMsg(t('directory.inviteSuccess', { defaultValue: 'Invitation added' }));
      void loadAll();
      if (selectedCompanyId) void loadCompanyDetail(selectedCompanyId);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  // Add Document
  const submitDocument = async () => {
    if (!selectedCompanyId || !docForm.number.trim()) return;
    try {
      const headers = authHeaders();
      const r = await fetch(`${API}/api/admin/directory/companies/${encodeURIComponent(selectedCompanyId)}/documents`, {
        method: 'POST',
        headers,
        body: JSON.stringify(docForm),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(j.detail || `HTTP ${r.status}`);
      }
      setDocModalOpen(false);
      setDocForm({
        number: '',
        date: new Date().toISOString().slice(0, 10),
        kind: 'invoice',
        currency: 'EUR',
        terms: 'Net 30',
        status: 'open',
        lines: [{ description: '', qty: 1, unit_price: 0 }],
      });
      void loadCompanyDetail(selectedCompanyId);
      void loadAll();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  // Toggle Document Paid Status
  const toggleDocumentPaid = async (doc: CommercialDocument) => {
    if (!selectedCompanyId) return;
    try {
      const headers = authHeaders();
      const nextStatus = doc.status === 'paid' ? 'open' : 'paid';
      const r = await fetch(
        `${API}/api/admin/directory/companies/${encodeURIComponent(selectedCompanyId)}/documents/${encodeURIComponent(doc.id)}`,
        {
          method: 'PATCH',
          headers,
          body: JSON.stringify({ status: nextStatus }),
        }
      );
      if (r.ok) {
        void loadCompanyDetail(selectedCompanyId);
        void loadAll();
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  // Filtered lists
  const filteredCompanies = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return companies;
    return companies.filter(
      (c) =>
        c.legal_name.toLowerCase().includes(q) ||
        (c.local_name && c.local_name.toLowerCase().includes(q)) ||
        (c.country && c.country.toLowerCase().includes(q)) ||
        (c.reg_no && c.reg_no.toLowerCase().includes(q)) ||
        (c.vat_no && c.vat_no.toLowerCase().includes(q)) ||
        (c.domains && c.domains.some((d) => d.toLowerCase().includes(q)))
    );
  }, [companies, search]);

  const filteredPeople = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return people;
    return people.filter(
      (p) =>
        p.name.toLowerCase().includes(q) ||
        p.emails.some((e) => e.toLowerCase().includes(q)) ||
        (p.user_id && p.user_id.toLowerCase().includes(q)) ||
        (p.companies && p.companies.some((c) => c.legal_name.toLowerCase().includes(q)))
    );
  }, [people, search]);

  return (
    <Box sx={{ pb: 4 }}>
      {/* Title & Top Toolbar */}
      <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 2, flexWrap: 'wrap', gap: 1 }}>
        <Box>
          <Typography sx={{ fontSize: 18, fontWeight: 800, color: 'var(--text-0)' }}>
            {t('directory.title', { defaultValue: 'Companies & people' })}
          </Typography>
          <Typography sx={{ fontSize: 12, color: 'var(--text-3)' }}>
            {t('directory.subtitle', { defaultValue: 'Directory of enterprise partners, OEMs, people, and relations' })}
          </Typography>
        </Box>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <Button
            size="small"
            variant="outlined"
            startIcon={<SyncIcon />}
            onClick={() => void runBackfill()}
            sx={{ textTransform: 'none', fontSize: 12 }}
          >
            {t('directory.backfillNda', { defaultValue: 'Backfill from NDA' })}
          </Button>
          <Button
            size="small"
            variant="contained"
            startIcon={<AddIcon />}
            onClick={() => {
              setEditingCompany({
                legal_name: '',
                local_name: '',
                country: '',
                address: '',
                phone: '',
                website: '',
                reg_no: '',
                vat_no: '',
                roles: ['customer'],
                domains: [],
                notes: '',
              });
              setCompanyModalOpen(true);
            }}
            sx={{ textTransform: 'none', fontSize: 12, bgcolor: 'var(--accent, #3b82f6)' }}
          >
            {t('directory.newCompany', { defaultValue: 'New Company' })}
          </Button>
          <IconButton size="small" onClick={() => void loadAll()} disabled={loading} title="Refresh">
            <RefreshIcon fontSize="small" />
          </IconButton>
        </Box>
      </Box>

      {/* Notifications */}
      {error && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
      {successMsg && (
        <Alert severity="success" sx={{ mb: 2 }} onClose={() => setSuccessMsg(null)}>
          {successMsg}
        </Alert>
      )}

      {/* Tabs */}
      <Box sx={{ borderBottom: 1, borderColor: 'var(--line-soft)', mb: 2, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <SubTabs value={tab} onChange={setTab} ariaLabel={t('directory.companiesTab', { defaultValue: 'Companies' })} items={[
          { id: 'companies', label: `${t('directory.companiesTab', { defaultValue: 'Companies' })} (${companies.length})` },
          { id: 'people', label: `${t('directory.peopleTab', { defaultValue: 'People' })} (${people.length})` },
          { id: 'proposed', label: (
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
              <span>{t('directory.proposedTab', { defaultValue: 'Proposed Affiliations' })}</span>
              {proposed.length > 0 && (
                <Chip
                  label={proposed.length}
                  size="small"
                  sx={{ height: 18, fontSize: 10, fontWeight: 700, bgcolor: '#ef4444', color: '#fff' }}
                />
              )}
            </Box>
          ) },
        ]} />
        <TextField
          size="small"
          placeholder={t('directory.searchPlaceholder', { defaultValue: 'Search…' })}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          sx={{ width: 220, '& input': { fontSize: 12, py: 0.6 } }}
        />
      </Box>

      {loading && (
        <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}>
          <CircularProgress size={24} />
        </Box>
      )}

      {/* ─── TAB 1: COMPANIES ──────────────────────────────────────────────── */}
      {!loading && tab === 'companies' && (
        <Paper sx={{ ...PANEL, overflow: 'hidden' }}>
          {agreementInstalled === false && (
            <Typography sx={{ fontSize: 11, color: 'var(--text-3)', px: 1.5, py: 0.75 }}>
              {t('directory.agreementNotInstalled', { defaultValue: 'Agreement: text not installed (users are not asked to accept it yet).' })}
            </Typography>
          )}
          <Table size="small">
            <TableHead sx={{ bgcolor: 'var(--panel)' }}>
              <TableRow>
                <TableCell sx={{ fontSize: 11, fontWeight: 700 }}>{t('directory.colName', { defaultValue: 'Company Name' })}</TableCell>
                <TableCell sx={{ fontSize: 11, fontWeight: 700 }}>{t('directory.colCountry', { defaultValue: 'Country' })}</TableCell>
                <TableCell sx={{ fontSize: 11, fontWeight: 700 }}>{t('directory.colRoles', { defaultValue: 'Roles' })}</TableCell>
                <TableCell sx={{ fontSize: 11, fontWeight: 700 }}>{t('directory.colNda', { defaultValue: 'NDA' })}</TableCell>
                <TableCell sx={{ fontSize: 11, fontWeight: 700 }}>{t('directory.colVerification', { defaultValue: 'Verification' })}</TableCell>
                <TableCell sx={{ fontSize: 11, fontWeight: 700 }}>{t('directory.colAgreement', { defaultValue: 'Agreement' })}</TableCell>
                <TableCell sx={{ fontSize: 11, fontWeight: 700 }}>{t('directory.colPeople', { defaultValue: 'People' })}</TableCell>
                <TableCell sx={{ fontSize: 11, fontWeight: 700 }}>{t('directory.colDocuments', { defaultValue: 'Documents' })}</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {filteredCompanies.map((c) => {
                const docSum = c.documents_summary;
                return (
                  <TableRow
                    key={c.id}
                    hover
                    onClick={() => openCompanyDetail(c.id)}
                    sx={{ cursor: 'pointer', '&:last-child td, &:last-child th': { border: 0 } }}
                  >
                    <TableCell>
                      <Typography sx={{ fontSize: 12.5, fontWeight: 700, color: 'var(--text-0)' }}>
                        {c.legal_name}
                      </Typography>
                      {c.local_name && (
                        <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>
                          {c.local_name}
                        </Typography>
                      )}
                    </TableCell>
                    <TableCell>
                      <Typography sx={{ fontSize: 12, color: 'var(--text-1)' }}>
                        {c.country || '—'}
                      </Typography>
                    </TableCell>
                    <TableCell>
                      <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap' }}>
                        {c.roles && c.roles.length > 0 ? (
                          c.roles.map((r) => (
                            <Chip key={r} label={r} size="small" sx={{ fontSize: 10, height: 20 }} />
                          ))
                        ) : (
                          <Typography sx={{ fontSize: 11, color: 'var(--text-4)' }}>—</Typography>
                        )}
                      </Box>
                    </TableCell>
                    <TableCell>
                      {c.nda?.status === 'signed' ? (
                        <Chip
                          label={`Signed ${c.nda.date || ''}`}
                          size="small"
                          color="success"
                          variant="outlined"
                          sx={{ fontSize: 10, height: 20 }}
                        />
                      ) : (
                        <Chip label="None" size="small" sx={{ fontSize: 10, height: 20, color: 'var(--text-4)' }} />
                      )}
                    </TableCell>
                    <TableCell>
                      {c.verification?.status && c.verification.status !== 'none' ? (
                        <Chip
                          label={c.verification.status.replace(/_/g, ' ')}
                          size="small"
                          color={c.verification.status === 'VERIFIED' ? 'success' : c.verification.status === 'DENIED' ? 'error' : 'warning'}
                          sx={{ fontSize: 10, height: 20 }}
                        />
                      ) : (
                        <Typography sx={{ fontSize: 11, color: 'var(--text-4)' }}>—</Typography>
                      )}
                    </TableCell>
                    <TableCell>
                      <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>{companyAgreementText(c.agreement)}</Typography>
                    </TableCell>
                    <TableCell>
                      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                        <Typography sx={{ fontSize: 12, fontWeight: 600 }}>
                          {c.people_count || 0}
                        </Typography>
                        {Boolean(c.proposed_count && c.proposed_count > 0) && (
                          <Chip
                            label={`+${c.proposed_count} proposed`}
                            size="small"
                            sx={{ height: 18, fontSize: 9, bgcolor: '#fef3c7', color: '#b45309', fontWeight: 700 }}
                          />
                        )}
                      </Box>
                    </TableCell>
                    <TableCell>
                      {docSum && docSum.total_count > 0 ? (
                        <Typography sx={{ fontSize: 11 }}>
                          {docSum.open_count > 0 && (
                            <span style={{ color: '#d97706', fontWeight: 600 }}>
                              {docSum.open_count} open ({docSum.currency} {docSum.open_total.toLocaleString()}){' '}
                            </span>
                          )}
                          {docSum.paid_count > 0 && (
                            <span style={{ color: '#16a34a' }}>
                              · {docSum.paid_count} paid ({docSum.currency} {docSum.paid_total.toLocaleString()})
                            </span>
                          )}
                        </Typography>
                      ) : (
                        <Typography sx={{ fontSize: 11, color: 'var(--text-4)' }}>—</Typography>
                      )}
                    </TableCell>
                  </TableRow>
                );
              })}
              {filteredCompanies.length === 0 && (
                <TableRow>
                  <TableCell colSpan={8} sx={{ textAlign: 'center', py: 4, color: 'var(--text-3)' }}>
                    {t('directory.noCompaniesFound', { defaultValue: 'No companies match the search.' })}
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </Paper>
      )}

      {/* ─── TAB 2: PEOPLE ─────────────────────────────────────────────────── */}
      {!loading && tab === 'people' && (
        <Paper sx={{ ...PANEL, overflow: 'hidden' }}>
          <Table size="small">
            <TableHead sx={{ bgcolor: 'var(--panel)' }}>
              <TableRow>
                <TableCell sx={{ fontSize: 11, fontWeight: 700 }}>{t('directory.colPersonName', { defaultValue: 'Name' })}</TableCell>
                <TableCell sx={{ fontSize: 11, fontWeight: 700 }}>{t('directory.colEmail', { defaultValue: 'E-mail' })}</TableCell>
                <TableCell sx={{ fontSize: 11, fontWeight: 700 }}>{t('directory.colUserAccount', { defaultValue: 'User Account' })}</TableCell>
                <TableCell sx={{ fontSize: 11, fontWeight: 700 }}>{t('directory.colAffiliatedCompanies', { defaultValue: 'Companies' })}</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {filteredPeople.map((p) => (
                <TableRow key={p.id}>
                  <TableCell>
                    <Typography sx={{ fontSize: 12.5, fontWeight: 700, color: 'var(--text-0)' }}>
                      {p.name}
                    </Typography>
                  </TableCell>
                  <TableCell>
                    <Typography sx={{ fontSize: 12 }}>
                      {p.emails.join(', ') || '—'}
                    </Typography>
                  </TableCell>
                  <TableCell>
                    {p.user_id ? (
                      <Chip label={p.user_id} size="small" variant="outlined" color="primary" sx={{ fontSize: 10, height: 20 }} />
                    ) : (
                      <Typography sx={{ fontSize: 11, color: 'var(--text-4)' }}>No account</Typography>
                    )}
                  </TableCell>
                  <TableCell>
                    <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap' }}>
                      {p.companies && p.companies.length > 0 ? (
                        p.companies.map((c) => (
                          <Chip
                            key={c.affiliation_id}
                            label={`${c.legal_name} (${c.status})`}
                            size="small"
                            color={c.status === 'confirmed' ? 'default' : c.status === 'proposed' ? 'warning' : 'default'}
                            variant={c.status === 'confirmed' ? 'filled' : 'outlined'}
                            onClick={() => openCompanyDetail(c.company_id)}
                            sx={{ fontSize: 10, height: 20, cursor: 'pointer' }}
                          />
                        ))
                      ) : (
                        <Typography sx={{ fontSize: 11, color: 'var(--text-4)' }}>—</Typography>
                      )}
                    </Box>
                  </TableCell>
                </TableRow>
              ))}
              {filteredPeople.length === 0 && (
                <TableRow>
                  <TableCell colSpan={4} sx={{ textAlign: 'center', py: 4, color: 'var(--text-3)' }}>
                    {t('directory.noPeopleFound', { defaultValue: 'No people match the search.' })}
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </Paper>
      )}

      {/* ─── TAB 3: PROPOSED AFFILIATIONS QUEUE ─────────────────────────────── */}
      {!loading && tab === 'proposed' && (
        <Paper sx={{ ...PANEL, overflow: 'hidden' }}>
          <Table size="small">
            <TableHead sx={{ bgcolor: 'var(--panel)' }}>
              <TableRow>
                <TableCell sx={{ fontSize: 11, fontWeight: 700 }}>{t('directory.colPerson', { defaultValue: 'Person' })}</TableCell>
                <TableCell sx={{ fontSize: 11, fontWeight: 700 }}>{t('directory.colCompany', { defaultValue: 'Company' })}</TableCell>
                <TableCell sx={{ fontSize: 11, fontWeight: 700 }}>{t('directory.colTitle', { defaultValue: 'Title / Role' })}</TableCell>
                <TableCell sx={{ fontSize: 11, fontWeight: 700 }}>{t('directory.colSource', { defaultValue: 'Source' })}</TableCell>
                <TableCell sx={{ fontSize: 11, fontWeight: 700, textAlign: 'right' }}>{t('directory.colActions', { defaultValue: 'Actions' })}</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {proposed.map((aff) => (
                <TableRow key={aff.id}>
                  <TableCell>
                    <Typography sx={{ fontSize: 12.5, fontWeight: 700 }}>{aff.person_name}</Typography>
                    <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>
                      {aff.person_emails.join(', ')}
                    </Typography>
                  </TableCell>
                  <TableCell>
                    <Typography sx={{ fontSize: 12.5, fontWeight: 600 }}>{aff.company_name}</Typography>
                    <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>{aff.company_country}</Typography>
                  </TableCell>
                  <TableCell>
                    <Typography sx={{ fontSize: 12 }}>{aff.title || 'Member'}</Typography>
                  </TableCell>
                  <TableCell>
                    <Chip label={aff.source} size="small" sx={{ fontSize: 10, height: 20 }} />
                  </TableCell>
                  <TableCell sx={{ textAlign: 'right' }}>
                    <Button
                      size="small"
                      color="success"
                      variant="contained"
                      startIcon={<CheckCircleOutlineIcon />}
                      onClick={() => void handleConfirmAffiliation(aff.id)}
                      sx={{ mr: 1, textTransform: 'none', fontSize: 11, py: 0.2 }}
                    >
                      {t('directory.btnConfirm', { defaultValue: 'Confirm' })}
                    </Button>
                    <Button
                      size="small"
                      color="inherit"
                      variant="outlined"
                      startIcon={<CancelOutlinedIcon />}
                      onClick={() => void handleEndAffiliation(aff.id)}
                      sx={{ textTransform: 'none', fontSize: 11, py: 0.2 }}
                    >
                      {t('directory.btnReject', { defaultValue: 'Reject' })}
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
              {proposed.length === 0 && (
                <TableRow>
                  <TableCell colSpan={5} sx={{ textAlign: 'center', py: 4, color: 'var(--text-3)' }}>
                    {t('directory.noProposedWaiting', { defaultValue: 'No proposed affiliations waiting for review.' })}
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </Paper>
      )}

      {/* ─── COMPANY DETAIL DRAWER ─────────────────────────────────────────── */}
      <Drawer
        anchor="right"
        open={Boolean(selectedCompanyId)}
        onClose={closeCompanyDetail}
        PaperProps={{ sx: { width: { xs: '100%', sm: 650 }, p: 3, bgcolor: 'var(--panel-1)' } }}
      >
        {detailLoading || !detailCompany ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}>
            <CircularProgress size={24} />
          </Box>
        ) : (
          <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%', overflowY: 'auto' }}>
            {/* Header */}
            <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', mb: 2 }}>
              <Box>
                <Typography sx={{ fontSize: 20, fontWeight: 800, color: 'var(--text-0)' }}>
                  {detailCompany.legal_name}
                </Typography>
                {detailCompany.local_name && (
                  <Typography sx={{ fontSize: 13, color: 'var(--text-2)' }}>
                    {detailCompany.local_name}
                  </Typography>
                )}
                <Typography sx={{ fontSize: 11, color: 'var(--text-4)', mt: 0.5 }}>
                  ID: {detailCompany.id} · Source: {detailCompany.source}
                </Typography>
              </Box>
              <IconButton onClick={closeCompanyDetail} size="small">
                <CloseIcon />
              </IconButton>
            </Box>

            {/* Quick Actions */}
            <Box sx={{ display: 'flex', gap: 1, mb: 3 }}>
              <Button
                size="small"
                variant="outlined"
                onClick={() => {
                  setEditingCompany({ ...detailCompany });
                  setCompanyModalOpen(true);
                }}
                sx={{ textTransform: 'none', fontSize: 11 }}
              >
                {t('directory.editCompany', { defaultValue: 'Edit Company' })}
              </Button>
              <Button
                size="small"
                variant="outlined"
                onClick={() => {
                  setInviteCompanyId(detailCompany.id);
                  setInviteModalOpen(true);
                }}
                sx={{ textTransform: 'none', fontSize: 11 }}
              >
                {t('directory.invitePerson', { defaultValue: 'Add Person / Invite' })}
              </Button>
              <Button
                size="small"
                variant="outlined"
                onClick={() => setDocModalOpen(true)}
                sx={{ textTransform: 'none', fontSize: 11 }}
              >
                {t('directory.addDocument', { defaultValue: 'Add Document' })}
              </Button>
            </Box>

            {/* Basic Info Cards */}
            <Paper sx={{ ...PANEL, p: 2, mb: 3 }}>
              <Typography sx={{ fontSize: 12, fontWeight: 700, color: 'var(--text-2)', mb: 1 }}>
                {t('directory.companyDetails', { defaultValue: 'Company Details' })}
              </Typography>
              <Box sx={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 1.5, fontSize: 12 }}>
                <Box>
                  <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>Country</Typography>
                  <Typography sx={{ fontWeight: 600 }}>{detailCompany.country || '—'}</Typography>
                </Box>
                <Box>
                  <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>Registration No.</Typography>
                  <Typography sx={{ fontWeight: 600 }}>{detailCompany.reg_no || '—'}</Typography>
                </Box>
                <Box>
                  <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>VAT No.</Typography>
                  <Typography sx={{ fontWeight: 600 }}>{detailCompany.vat_no || '—'}</Typography>
                </Box>
                <Box>
                  <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>Website</Typography>
                  <Typography sx={{ fontWeight: 600 }}>{detailCompany.website || '—'}</Typography>
                </Box>
                <Box sx={{ gridColumn: 'span 2' }}>
                  <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>Address</Typography>
                  <Typography sx={{ fontWeight: 500 }}>{detailCompany.address || '—'}</Typography>
                </Box>
                <Box sx={{ gridColumn: 'span 2' }}>
                  <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>Associated E-mail Domains</Typography>
                  <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap', mt: 0.5 }}>
                    {detailCompany.domains && detailCompany.domains.length > 0 ? (
                      detailCompany.domains.map((d) => (
                        <Chip key={d} label={d} size="small" sx={{ fontSize: 10, height: 20 }} />
                      ))
                    ) : (
                      <Typography sx={{ fontSize: 11, color: 'var(--text-4)' }}>—</Typography>
                    )}
                  </Box>
                </Box>
              </Box>
            </Paper>

            {/* People & Affiliations */}
            <Paper sx={{ ...PANEL, p: 2, mb: 3 }}>
              <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 1 }}>
                <Typography sx={{ fontSize: 12, fontWeight: 700, color: 'var(--text-2)' }}>
                  {t('directory.peopleInCompany', { defaultValue: 'Affiliated People' })} ({detailCompany.people?.length || 0})
                </Typography>
              </Box>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell sx={{ fontSize: 10, fontWeight: 700 }}>Name</TableCell>
                    <TableCell sx={{ fontSize: 10, fontWeight: 700 }}>Title</TableCell>
                    <TableCell sx={{ fontSize: 10, fontWeight: 700 }}>Status</TableCell>
                    <TableCell sx={{ fontSize: 10, fontWeight: 700, textAlign: 'right' }}>Actions</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {(detailCompany.people || []).map((p) => {
                    const aff = p.affiliation;
                    return (
                      <TableRow key={p.id}>
                        <TableCell>
                          <Typography sx={{ fontSize: 12, fontWeight: 600 }}>{p.name}</Typography>
                          <Typography sx={{ fontSize: 10, color: 'var(--text-3)' }}>
                            {p.emails.join(', ')}
                          </Typography>
                        </TableCell>
                        <TableCell sx={{ fontSize: 11 }}>{aff?.title || '—'}</TableCell>
                        <TableCell>
                          <Chip
                            label={aff?.status}
                            size="small"
                            color={aff?.status === 'confirmed' ? 'success' : aff?.status === 'proposed' ? 'warning' : 'default'}
                            sx={{ fontSize: 9, height: 18 }}
                          />
                        </TableCell>
                        <TableCell sx={{ textAlign: 'right' }}>
                          {aff?.status === 'proposed' && (
                            <Button
                              size="small"
                              color="success"
                              onClick={() => void handleConfirmAffiliation(aff.id)}
                              sx={{ textTransform: 'none', fontSize: 10, p: 0.2, minWidth: 0, mr: 1 }}
                            >
                              Confirm
                            </Button>
                          )}
                          {aff?.status !== 'ended' && (
                            <Button
                              size="small"
                              color="error"
                              onClick={() => void handleEndAffiliation(aff!.id)}
                              sx={{ textTransform: 'none', fontSize: 10, p: 0.2, minWidth: 0 }}
                            >
                              End
                            </Button>
                          )}
                        </TableCell>
                      </TableRow>
                    );
                  })}
                  {(!detailCompany.people || detailCompany.people.length === 0) && (
                    <TableRow>
                      <TableCell colSpan={4} sx={{ textAlign: 'center', py: 2, color: 'var(--text-4)', fontSize: 11 }}>
                        No people affiliated with this company yet.
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            </Paper>

            {/* Commercial Documents */}
            <Paper sx={{ ...PANEL, p: 2, mb: 3 }}>
              <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 1 }}>
                <Typography sx={{ fontSize: 12, fontWeight: 700, color: 'var(--text-2)' }}>
                  {t('directory.commercialDocs', { defaultValue: 'Commercial Documents' })} ({detailCompany.documents?.length || 0})
                </Typography>
              </Box>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell sx={{ fontSize: 10, fontWeight: 700 }}>Number</TableCell>
                    <TableCell sx={{ fontSize: 10, fontWeight: 700 }}>Kind</TableCell>
                    <TableCell sx={{ fontSize: 10, fontWeight: 700 }}>Total</TableCell>
                    <TableCell sx={{ fontSize: 10, fontWeight: 700 }}>Status</TableCell>
                    <TableCell sx={{ fontSize: 10, fontWeight: 700, textAlign: 'right' }}>Action</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {(detailCompany.documents || []).map((doc) => (
                    <TableRow key={doc.id}>
                      <TableCell sx={{ fontSize: 11, fontWeight: 600 }}>{doc.number}</TableCell>
                      <TableCell sx={{ fontSize: 11 }}>{doc.kind}</TableCell>
                      <TableCell sx={{ fontSize: 11, fontWeight: 600 }}>
                        {doc.currency} {doc.total.toLocaleString()}
                      </TableCell>
                      <TableCell>
                        <Chip
                          label={doc.status}
                          size="small"
                          color={doc.status === 'paid' ? 'success' : doc.status === 'open' ? 'warning' : 'default'}
                          sx={{ fontSize: 9, height: 18 }}
                        />
                      </TableCell>
                      <TableCell sx={{ textAlign: 'right' }}>
                        <Button
                          size="small"
                          variant="outlined"
                          onClick={() => void toggleDocumentPaid(doc)}
                          sx={{ textTransform: 'none', fontSize: 10, py: 0.2 }}
                        >
                          {doc.status === 'paid' ? 'Mark Open' : 'Mark Paid'}
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                  {(!detailCompany.documents || detailCompany.documents.length === 0) && (
                    <TableRow>
                      <TableCell colSpan={5} sx={{ textAlign: 'center', py: 2, color: 'var(--text-4)', fontSize: 11 }}>
                        No commercial documents recorded.
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            </Paper>

            {/* Linked Productions (Information Only) */}
            <Paper sx={{ ...PANEL, p: 2, mb: 3 }}>
              <Typography sx={{ fontSize: 12, fontWeight: 700, color: 'var(--text-2)', mb: 1 }}>
                {t('directory.linkedProductions', { defaultValue: 'Linked Productions (Information Only)' })}
              </Typography>
              {detailCompany.productions && detailCompany.productions.length > 0 ? (
                <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
                  {detailCompany.productions.map((prod) => (
                    <Box
                      key={`${prod.die}-${prod.config}`}
                      sx={{ p: 1, bgcolor: 'var(--panel)', borderRadius: 1, border: '1px solid var(--line-soft)', fontSize: 11 }}
                    >
                      <Typography sx={{ fontSize: 12, fontWeight: 700 }}>{prod.title}</Typography>
                      <Typography sx={{ color: 'var(--text-3)', fontSize: 10 }}>
                        Die: {prod.die} · Config: {prod.config} · Members: {prod.matched_members.join(', ')}
                      </Typography>
                    </Box>
                  ))}
                </Box>
              ) : (
                <Typography sx={{ fontSize: 11, color: 'var(--text-4)' }}>
                  No productions linked to this company’s members.
                </Typography>
              )}
            </Paper>

            {/* Audit Trail */}
            <Paper sx={{ ...PANEL, p: 2 }}>
              <Typography sx={{ fontSize: 12, fontWeight: 700, color: 'var(--text-2)', mb: 1 }}>
                {t('directory.auditTrail', { defaultValue: 'Audit Trail' })}
              </Typography>
              <Box sx={{ maxHeight: 200, overflowY: 'auto' }}>
                {(detailCompany.audit_trail || []).map((a, i) => (
                  <Box key={i} sx={{ py: 0.5, borderBottom: '1px solid var(--line-soft)', fontSize: 10 }}>
                    <Typography sx={{ fontSize: 10, color: 'var(--text-3)' }}>
                      {a.iso} · {a.actor}
                    </Typography>
                    <Typography sx={{ fontWeight: 600 }}>{a.action}</Typography>
                  </Box>
                ))}
              </Box>
            </Paper>
          </Box>
        )}
      </Drawer>

      {/* ─── MODAL: CREATE / EDIT COMPANY ───────────────────────────────────── */}
      <Dialog open={companyModalOpen} onClose={() => setCompanyModalOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle sx={{ fontSize: 16, fontWeight: 800 }}>
          {editingCompany?.id ? 'Edit Company' : 'New Company'}
        </DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 1.5, pt: 1 }}>
          <TextField
            label="Legal Name"
            size="small"
            fullWidth
            required
            value={editingCompany?.legal_name || ''}
            onChange={(e) => setEditingCompany((c) => ({ ...c, legal_name: e.target.value }))}
          />
          <TextField
            label="Local Name (e.g. Chinese)"
            size="small"
            fullWidth
            value={editingCompany?.local_name || ''}
            onChange={(e) => setEditingCompany((c) => ({ ...c, local_name: e.target.value }))}
          />
          <Box sx={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 1.5 }}>
            <TextField
              label="Country (ISO Code)"
              size="small"
              value={editingCompany?.country || ''}
              onChange={(e) => setEditingCompany((c) => ({ ...c, country: e.target.value }))}
            />
            <TextField
              label="Registration No."
              size="small"
              value={editingCompany?.reg_no || ''}
              onChange={(e) => setEditingCompany((c) => ({ ...c, reg_no: e.target.value }))}
            />
          </Box>
          <Box sx={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 1.5 }}>
            <TextField
              label="VAT No."
              size="small"
              value={editingCompany?.vat_no || ''}
              onChange={(e) => setEditingCompany((c) => ({ ...c, vat_no: e.target.value }))}
            />
            <TextField
              label="Phone"
              size="small"
              value={editingCompany?.phone || ''}
              onChange={(e) => setEditingCompany((c) => ({ ...c, phone: e.target.value }))}
            />
          </Box>
          <TextField
            label="Website"
            size="small"
            fullWidth
            value={editingCompany?.website || ''}
            onChange={(e) => setEditingCompany((c) => ({ ...c, website: e.target.value }))}
          />
          <TextField
            label="Address"
            size="small"
            fullWidth
            multiline
            rows={2}
            value={editingCompany?.address || ''}
            onChange={(e) => setEditingCompany((c) => ({ ...c, address: e.target.value }))}
          />
          <TextField
            label="E-mail Domains (comma separated)"
            size="small"
            fullWidth
            value={(editingCompany?.domains || []).join(', ')}
            onChange={(e) =>
              setEditingCompany((c) => ({
                ...c,
                domains: e.target.value.split(',').map((s) => s.trim()).filter(Boolean),
              }))
            }
          />
          <TextField
            label="Notes"
            size="small"
            fullWidth
            multiline
            rows={2}
            value={editingCompany?.notes || ''}
            onChange={(e) => setEditingCompany((c) => ({ ...c, notes: e.target.value }))}
          />
        </DialogContent>
        <DialogActions sx={{ p: 2 }}>
          <Button onClick={() => setCompanyModalOpen(false)} sx={{ textTransform: 'none' }}>
            Cancel
          </Button>
          <Button variant="contained" onClick={() => void saveCompany()} sx={{ textTransform: 'none' }}>
            Save
          </Button>
        </DialogActions>
      </Dialog>

      {/* ─── MODAL: INVITE PERSON TO COMPANY ───────────────────────────────── */}
      <Dialog open={inviteModalOpen} onClose={() => setInviteModalOpen(false)} maxWidth="xs" fullWidth>
        <DialogTitle sx={{ fontSize: 16, fontWeight: 800 }}>
          {t('directory.invitePersonModalTitle', { defaultValue: 'Add Person / Send Invitation' })}
        </DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 1.5, pt: 1 }}>
          <TextField
            label="E-mail"
            size="small"
            fullWidth
            required
            type="email"
            value={inviteForm.email}
            onChange={(e) => setInviteForm((f) => ({ ...f, email: e.target.value }))}
          />
          <TextField
            label="Full Name (optional)"
            size="small"
            fullWidth
            value={inviteForm.name}
            onChange={(e) => setInviteForm((f) => ({ ...f, name: e.target.value }))}
          />
          <TextField
            label="Role / Title at Company (optional)"
            size="small"
            fullWidth
            value={inviteForm.title}
            onChange={(e) => setInviteForm((f) => ({ ...f, title: e.target.value }))}
          />
        </DialogContent>
        <DialogActions sx={{ p: 2 }}>
          <Button onClick={() => setInviteModalOpen(false)} sx={{ textTransform: 'none' }}>
            Cancel
          </Button>
          <Button variant="contained" onClick={() => void submitInvite()} sx={{ textTransform: 'none' }}>
            Add
          </Button>
        </DialogActions>
      </Dialog>

      {/* ─── MODAL: ADD COMMERCIAL DOCUMENT ─────────────────────────────────── */}
      <Dialog open={docModalOpen} onClose={() => setDocModalOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle sx={{ fontSize: 16, fontWeight: 800 }}>
          {t('directory.addDocModalTitle', { defaultValue: 'Add Commercial Document' })}
        </DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 1.5, pt: 1 }}>
          <Alert severity="info" sx={{ fontSize: 11, py: 0.5 }}>
            {t('directory.bankNotice', {
              defaultValue: 'Never store bank account numbers or SWIFT details. These are internal and kept separate.',
            })}
          </Alert>
          <Box sx={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 1.5 }}>
            <TextField
              label="Document Number"
              size="small"
              required
              value={docForm.number}
              onChange={(e) => setDocForm((f) => ({ ...f, number: e.target.value }))}
            />
            <TextField
              label="Date"
              type="date"
              size="small"
              value={docForm.date}
              onChange={(e) => setDocForm((f) => ({ ...f, date: e.target.value }))}
            />
          </Box>
          <Box sx={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 1.5 }}>
            <FormControl size="small">
              <InputLabel>Kind</InputLabel>
              <Select
                value={docForm.kind}
                label="Kind"
                onChange={(e) => setDocForm((f) => ({ ...f, kind: e.target.value as any }))}
              >
                <MenuItem value="invoice">Invoice</MenuItem>
                <MenuItem value="proforma">Proforma</MenuItem>
                <MenuItem value="PO">Purchase Order</MenuItem>
              </Select>
            </FormControl>
            <TextField
              label="Currency"
              size="small"
              value={docForm.currency}
              onChange={(e) => setDocForm((f) => ({ ...f, currency: e.target.value }))}
            />
            <FormControl size="small">
              <InputLabel>Status</InputLabel>
              <Select
                value={docForm.status}
                label="Status"
                onChange={(e) => setDocForm((f) => ({ ...f, status: e.target.value as any }))}
              >
                <MenuItem value="open">Open</MenuItem>
                <MenuItem value="paid">Paid</MenuItem>
                <MenuItem value="cancelled">Cancelled</MenuItem>
              </Select>
            </FormControl>
          </Box>
          <TextField
            label="Line Description"
            size="small"
            fullWidth
            value={docForm.lines[0]?.description || ''}
            onChange={(e) =>
              setDocForm((f) => ({
                ...f,
                lines: [{ ...f.lines[0], description: e.target.value }],
              }))
            }
          />
          <Box sx={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 1.5 }}>
            <TextField
              label="Quantity"
              type="number"
              size="small"
              value={docForm.lines[0]?.qty || 1}
              onChange={(e) =>
                setDocForm((f) => ({
                  ...f,
                  lines: [{ ...f.lines[0], qty: parseFloat(e.target.value) || 0 }],
                }))
              }
            />
            <TextField
              label="Unit Price"
              type="number"
              size="small"
              value={docForm.lines[0]?.unit_price || 0}
              onChange={(e) =>
                setDocForm((f) => ({
                  ...f,
                  lines: [{ ...f.lines[0], unit_price: parseFloat(e.target.value) || 0 }],
                }))
              }
            />
          </Box>
          <TextField
            label="Terms"
            size="small"
            fullWidth
            value={docForm.terms}
            onChange={(e) => setDocForm((f) => ({ ...f, terms: e.target.value }))}
          />
        </DialogContent>
        <DialogActions sx={{ p: 2 }}>
          <Button onClick={() => setDocModalOpen(false)} sx={{ textTransform: 'none' }}>
            Cancel
          </Button>
          <Button variant="contained" onClick={() => void submitDocument()} sx={{ textTransform: 'none' }}>
            Save Document
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default DirectorySection;
