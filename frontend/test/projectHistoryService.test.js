import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';

import {
  getProjectAnalysis,
  getProjectAnalysisHistory,
  getProjectHistory,
} from '../src/services/projectHistoryService.js';

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
});

test('loads paginated project history with the Firebase bearer token', async () => {
  globalThis.fetch = async (url, options) => {
    assert.equal(url, '/api/projects/history?page_size=10&cursor=next%2Fpage');
    assert.equal(options.headers.Authorization, 'Bearer history-token');
    return new Response(JSON.stringify({
      items: [{ id: 'project-1', file_count: 2 }],
      next_cursor: null,
    }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  };

  const page = await getProjectHistory(
    { getIdToken: async () => 'history-token' },
    { pageSize: 10, cursor: 'next/page' },
  );
  assert.equal(page.items[0].file_count, 2);
});

test('loads a selected project analysis and its findings', async () => {
  globalThis.fetch = async (url) => {
    assert.equal(
      url,
      '/api/projects/history/project%2F1/analyses/run%2F2',
    );
    return new Response(JSON.stringify({
      id: 'run/2',
      findings: [{ title: 'Saved issue', description: 'Details' }],
    }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  };

  const detail = await getProjectAnalysis(
    'project/1',
    'run/2',
    { getIdToken: async () => 'history-token' },
  );
  assert.equal(detail.findings[0].title, 'Saved issue');
});

test('surfaces backend errors when loading project analyses', async () => {
  globalThis.fetch = async (url) => {
    assert.equal(url, '/api/projects/history/project-1/analyses?page_size=5');
    return new Response(JSON.stringify({ detail: 'Project history not found.' }), {
      status: 404,
      headers: { 'Content-Type': 'application/json' },
    });
  };

  await assert.rejects(
    getProjectAnalysisHistory(
      'project-1',
      { getIdToken: async () => 'history-token' },
      { pageSize: 5 },
    ),
    /Project history not found/,
  );
});
