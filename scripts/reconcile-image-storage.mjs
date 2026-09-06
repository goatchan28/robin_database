import { readFileSync } from 'node:fs';

const env = Object.fromEntries(
  readFileSync('api/.env', 'utf8')
    .split(/\r?\n/)
    .filter((line) => line && !line.trim().startsWith('#'))
    .map((line) => {
      const separator = line.indexOf('=');
      return [line.slice(0, separator).trim(), line.slice(separator + 1).trim().replace(/^['\"]|['\"]$/g, '')];
    }),
);

const baseUrl = env.SUPABASE_URL?.replace(/\/$/, '');
const apiKey = env.SUPABASE_SERVICE_ROLE_KEY;
const bucket = 'identity-images';

if (!baseUrl || !apiKey) throw new Error('Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY in api/.env');

const headers = { apikey: apiKey, 'content-type': 'application/json' };
const pathParts = (value) => value.split('/').filter(Boolean);
const basename = (value) => pathParts(value).at(-1);
const topFolder = (value) => pathParts(value)[0] ?? '';
const slug = (value) => String(value ?? '').toLowerCase().replace(/[^a-z0-9]+/g, '');

async function request(url, options = {}) {
  const response = await fetch(url, options);
  const text = await response.text();
  if (!response.ok) throw new Error(`${options.method ?? 'GET'} ${url.replace(baseUrl, '')} failed (${response.status}): ${text.slice(0, 300)}`);
  return text ? JSON.parse(text) : null;
}

function rest(path, method = 'GET', body) {
  return request(`${baseUrl}/rest/v1/${path}`, {
    method,
    headers: { ...headers, Prefer: 'return=minimal' },
    body: body ? JSON.stringify(body) : undefined,
  });
}

async function listTree(prefix = '') {
  const rows = await request(`${baseUrl}/storage/v1/object/list/${bucket}`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ prefix, limit: 1000, offset: 0, sortBy: { column: 'name', order: 'asc' } }),
  });
  const paths = [];
  for (const row of rows) {
    const path = prefix ? `${prefix}/${row.name}` : row.name;
    if (row.id) paths.push(path);
    else paths.push(...await listTree(path));
  }
  return paths;
}

const [identities, links, storagePaths] = await Promise.all([
  rest('identities?select=id,display_name,external_ref&status=eq.active'),
  rest('identity_image_links?select=identity_id,image_id,status,identity_images(id,storage_path)&status=eq.active'),
  listTree(),
]);

const identityById = new Map(identities.map((identity) => [identity.id, identity]));
const actualPaths = new Set(storagePaths);
const candidateByFolderAndName = new Map();
const candidateByName = new Map();
for (const path of storagePaths) {
  if (basename(path) === '.emptyFolderPlaceholder') continue;
  const key = `${slug(topFolder(path))}|${basename(path)}`;
  candidateByFolderAndName.set(key, [...(candidateByFolderAndName.get(key) ?? []), path]);
  candidateByName.set(basename(path), [...(candidateByName.get(basename(path)) ?? []), path]);
}

const usedPaths = new Set();
const plan = [];
const stale = [];
for (const link of links) {
  const identity = identityById.get(link.identity_id);
  const image = link.identity_images;
  if (!identity || !image) continue;
  let source = actualPaths.has(image.storage_path) ? image.storage_path : undefined;
  if (!source) {
    const candidates = (candidateByFolderAndName.get(`${slug(identity.display_name)}|${basename(image.storage_path)}`) ?? [])
      .filter((path) => !usedPaths.has(path));
    if (candidates.length === 1) source = candidates[0];
    if (candidates.length > 1) throw new Error(`Ambiguous source image for ${identity.display_name}`);
    if (!source) {
      const basenameCandidates = (candidateByName.get(basename(image.storage_path)) ?? [])
        .filter((path) => !usedPaths.has(path));
      if (basenameCandidates.length === 1) source = basenameCandidates[0];
      if (basenameCandidates.length > 1) throw new Error(`Ambiguous filename match for ${identity.display_name}`);
    }
  }
  if (!source) {
    stale.push({ link, image });
    continue;
  }
  if (usedPaths.has(source)) throw new Error(`The same Storage object matched multiple records: ${source}`);
  usedPaths.add(source);
  plan.push({ identity, link, image, source, target: `${slug(identity.display_name)}/${basename(source)}` });
}

const unknownFiles = storagePaths.filter((path) => basename(path) !== '.emptyFolderPlaceholder' && !usedPaths.has(path));
const targetPaths = new Set();
for (const step of plan) {
  if (targetPaths.has(step.target)) throw new Error(`Duplicate target path: ${step.target}`);
  targetPaths.add(step.target);
  if (actualPaths.has(step.target) && step.source !== step.target) throw new Error(`Target already exists: ${step.target}`);
}

if (plan.length !== links.length || stale.length !== 0) {
  throw new Error(`Audit changed: ${plan.length} moves, ${stale.length} stale links, ${unknownFiles.length} unmatched objects: ${unknownFiles.join(', ')}`);
}

console.log(`Validated ${plan.length} image moves, ${stale.length} stale links, and ${unknownFiles.length} untouched unmatched objects.`);

const moved = [];
const metadataUpdated = [];
const refsUpdated = [];
const archived = [];
try {
  let completed = 0;
  let nextIndex = 0;
  async function worker() {
    while (nextIndex < plan.length) {
      const step = plan[nextIndex++];
      if (step.source !== step.target) {
        await request(`${baseUrl}/storage/v1/object/move`, {
          method: 'POST',
          headers,
          body: JSON.stringify({ bucketId: bucket, sourceKey: step.source, destinationKey: step.target, destinationBucket: bucket }),
        });
        moved.push(step);
      }
      if (step.image.storage_path !== step.target) {
        await rest(`identity_images?id=eq.${encodeURIComponent(step.image.id)}`, 'PATCH', { storage_path: step.target });
        metadataUpdated.push(step);
      }
      completed += 1;
      if (completed % 10 === 0 || completed === plan.length) console.log(`Reconciled ${completed}/${plan.length}`);
    }
  }
  const work = await Promise.allSettled(Array.from({ length: 6 }, worker));
  const failure = work.find((result) => result.status === 'rejected');
  if (failure) throw failure.reason;
  for (const identity of identities) {
    const desired = slug(identity.display_name);
    if (identity.external_ref !== desired) {
      await rest(`identities?id=eq.${encodeURIComponent(identity.id)}`, 'PATCH', { external_ref: desired });
      refsUpdated.push(identity);
    }
  }
  for (const item of stale) {
    await rest(`identity_image_links?identity_id=eq.${encodeURIComponent(item.link.identity_id)}&image_id=eq.${encodeURIComponent(item.link.image_id)}`, 'PATCH', { status: 'archived' });
    archived.push(item);
  }
  console.log(JSON.stringify({ moved: moved.length, metadataUpdated: metadataUpdated.length, externalRefsNormalized: refsUpdated.length, staleLinksArchived: archived.length, untouchedUnknownFiles: unknownFiles }, null, 2));
} catch (error) {
  console.error(`Migration failed; reverting completed operations: ${error.message}`);
  for (const item of archived.reverse()) try { await rest(`identity_image_links?identity_id=eq.${encodeURIComponent(item.link.identity_id)}&image_id=eq.${encodeURIComponent(item.link.image_id)}`, 'PATCH', { status: 'active' }); } catch {}
  for (const identity of refsUpdated.reverse()) try { await rest(`identities?id=eq.${encodeURIComponent(identity.id)}`, 'PATCH', { external_ref: identity.external_ref }); } catch {}
  for (const step of metadataUpdated.reverse()) try { await rest(`identity_images?id=eq.${encodeURIComponent(step.image.id)}`, 'PATCH', { storage_path: step.image.storage_path }); } catch {}
  for (const step of moved.reverse()) try {
    await request(`${baseUrl}/storage/v1/object/move`, { method: 'POST', headers, body: JSON.stringify({ bucketId: bucket, sourceKey: step.target, destinationKey: step.source, destinationBucket: bucket }) });
  } catch {}
  throw error;
}
