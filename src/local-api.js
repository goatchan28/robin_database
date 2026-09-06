// Local development uses FastAPI directly. Deployed builds use the same
// origin, where Vercel routes `/api/*` to the server-side FastAPI function.
const baseUrl = import.meta.env.VITE_API_URL || (import.meta.env.DEV ? 'http://127.0.0.1:8082' : '/api');

const placeholder = (name) => `data:image/svg+xml,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="240" height="280"><rect width="100%" height="100%" fill="#09131a"/><circle cx="120" cy="93" r="48" fill="#315263"/><path d="M35 260c11-62 51-92 85-92s74 30 85 92" fill="#315263"/><text x="120" y="272" text-anchor="middle" font-family="Arial" font-size="12" fill="#a8c3ce">${name.slice(0, 18).toUpperCase()}</text></svg>`)}`;
const displayDate = (value) => value ? new Intl.DateTimeFormat(undefined, { year: 'numeric', month: 'short', day: '2-digit' }).format(new Date(value)) : '—';

async function request(path, options = {}) {
  const response = await fetch(`${baseUrl}${path}`, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(body.detail || `Local API request failed (${response.status})`);
    error.status = response.status;
    throw error;
  }
  return body;
}

export async function loadRecords() {
  const { items } = await request('/identities?status=active&page_size=100');
  return items.map((identity) => {
    const criminal = identity.criminal_record;
    const cases = (criminal?.cases || []).map((item) => [
      displayDate(item.incident_date), item.case_number, item.offense,
      item.jurisdiction, item.case_status,
    ]);
    return {
      id: identity.id, name: identity.display_name,
      status: criminal?.record_status || identity.record_status || identity.status?.toUpperCase() || 'ACTIVE',
      district: criminal?.primary_offense || identity.primary_offense || 'No offense recorded', count: identity.image_count || 0,
      updated: displayDate(identity.updated_at), image: placeholder(identity.display_name),
      threat: Math.max(0, Math.min(5, criminal?.wanted_level ?? identity.wanted_level ?? 0)), age: '—', sex: '—',
      criminalRecordId: criminal?.id || identity.criminal_record_id || null,
      criminal: criminal ? {
        id: criminal.id, status: criminal.record_status, wantedLevel: criminal.wanted_level,
        activeWarrant: criminal.active_warrant, warrantNumber: criminal.warrant_number,
        arrestCount: criminal.arrest_count, convictionCount: criminal.conviction_count,
        primaryOffense: criminal.primary_offense, lastArrestDate: displayDate(criminal.last_arrest_date), cases,
      } : { id: null, status: 'NO RECORD', cases: [] },
      assets: [], cases,
    };
  });
}

export async function loadIdentityImages(identityId) {
  const { items } = await request(`/identities/${identityId}/images`);
  return items.map((image) => ({
    id: image.id, image: image.signed_url,
    type: image.content_type?.replace('image/', '').toUpperCase() || 'IMAGE',
    date: displayDate(image.created_at),
  }));
}

export async function loadCases(record) {
  if (!record.criminalRecordId) return [];
  const { items } = await request(`/criminal-records/${record.criminalRecordId}/cases`);
  return items.map((item) => [displayDate(item.incident_date), item.case_number, item.offense, item.jurisdiction, item.case_status]);
}

export async function loadCriminalRecord(identityId) {
  let record;
  try {
    record = await request(`/identities/${identityId}/criminal-record`);
  } catch (error) {
    if (error.status === 404) return { id: null, status: 'NO RECORD', cases: [] };
    throw error;
  }
  return {
    id: record.id,
    status: record.record_status,
    wantedLevel: record.wanted_level,
    activeWarrant: record.active_warrant,
    warrantNumber: record.warrant_number,
    arrestCount: record.arrest_count,
    convictionCount: record.conviction_count,
    primaryOffense: record.primary_offense,
    lastArrestDate: displayDate(record.last_arrest_date),
    cases: (record.cases || []).map((item) => [
      displayDate(item.incident_date), item.case_number, item.offense,
      item.jurisdiction, item.case_status,
    ]),
  };
}

export async function uploadImageSet(identityId, files) {
  const formData = new FormData();
  files.forEach((file) => formData.append('files', file));
  formData.append('link_status', 'active');
  return request(`/identities/${identityId}/image-sets`, { method: 'POST', body: formData });
}

export async function intakeIdentityImages(fullName, files) {
  const formData = new FormData();
  formData.append('full_name', fullName);
  files.forEach((file) => formData.append('files', file));
  return request('/identity-intake', { method: 'POST', body: formData });
}

export async function healthCheck() {
  return request('/health');
}
