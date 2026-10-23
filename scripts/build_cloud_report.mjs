import { cpSync, existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import assert from 'node:assert/strict';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const sourceReport = join(root, 'ZavaFinOps.Report');
const outputReport = join(root, 'cloud-dev', 'ZavaFinOps.Report');
const modelId = '271d5b15-76c0-45b7-864b-897c8a630cec';
const pageId = createHash('sha256').update('zava-cloud-dev-assign-business-context').digest('hex').slice(0, 20);

assert.ok(existsSync(sourceReport), 'Local report template is missing.');
assert.ok(!existsSync(outputReport) || process.argv.includes('--overwrite'), 'Cloud report output already exists; rerun with --overwrite before publishing.');
cpSync(sourceReport, outputReport, { recursive: true });

const readJson = path => JSON.parse(readFileSync(join(outputReport, path), 'utf8'));
const writeJson = (path, value) => {
  const target = join(outputReport, path);
  mkdirSync(dirname(target), { recursive: true });
  writeFileSync(target, JSON.stringify(value, null, 2) + '\n');
};
const literal = value => ({ expr: { Literal: { Value: typeof value === 'string' ? `'${value.replaceAll("'", "''")}'` : String(value) } } });
const properties = value => [{ properties: value }];
const visualName = key => createHash('sha256').update(`${pageId}:${key}`).digest('hex').slice(0, 20);
const field = (table, name, measure = false) => ({
  [measure ? 'Measure' : 'Column']: {
    Expression: { SourceRef: { Entity: table } },
    Property: name
  }
});
const projection = (table, name, measure = false) => ({
  field: field(table, name, measure),
  queryRef: `${table}.${name}`,
  nativeQueryRef: name
});

const pbir = readJson('definition.pbir');
pbir.datasetReference = { byConnection: { connectionString: `semanticmodelid=${modelId}` } };
writeJson('definition.pbir', pbir);

const pages = readJson('definition/pages/pages.json');
assert.ok(!pages.pageOrder.includes(pageId), 'Assignment page already exists in the source report.');
pages.pageOrder.push(pageId);
writeJson('definition/pages/pages.json', pages);

const sourcePage = readJson(`definition/pages/${pages.pageOrder[0]}/page.json`);
writeJson(`definition/pages/${pageId}/page.json`, {
  ...sourcePage,
  name: pageId,
  displayName: 'Assign Business Context',
  visualInteractions: [
    { source: visualName('effective-date-input'), target: visualName('context-option'), type: 'NoFilter' },
    { source: visualName('reason-input'), target: visualName('context-option'), type: 'NoFilter' },
    { source: visualName('effective-date-input'), target: visualName('reason-input'), type: 'NoFilter' },
    { source: visualName('reason-input'), target: visualName('effective-date-input'), type: 'NoFilter' }
  ]
});

const visualRoot = join(outputReport, 'definition', 'pages', pageId, 'visuals');
const visualSchema = readJson(`definition/pages/${pages.pageOrder[0]}/visuals/${readdirSync(join(outputReport, 'definition', 'pages', pages.pageOrder[0], 'visuals'))[0]}/visual.json`).$schema;
let order = 0;

function addVisual(key, type, rect, title, queryState, objects = {}) {
  const name = visualName(key);
  const visual = {
    $schema: visualSchema,
    name,
    position: { x: rect[0], y: rect[1], width: rect[2], height: rect[3], z: order, tabOrder: order },
    visual: {
      visualType: type,
      objects,
      visualContainerObjects: {
        title: properties({ show: literal(Boolean(title)), text: literal(title), fontSize: literal(14) }),
        general: properties({ altText: literal(title || key) }),
        padding: properties({ top: literal(8), bottom: literal(8), left: literal(8), right: literal(8) }),
        border: properties({ show: literal(false) }),
        background: properties({ show: literal(true), color: { solid: { color: literal('#FFFFFF') } }, transparency: literal(0) })
      }
    }
  };
  if (queryState) visual.visual.query = { queryState };
  order += 1000;
  writeJson(`definition/pages/${pageId}/visuals/${name}/visual.json`, visual);
  return visual;
}

function addText(key, rect, value, size = 16) {
  const visual = addVisual(key, 'textbox', rect, '', undefined, {
    general: properties({ paragraphs: [{ textRuns: [{ value, textStyle: { fontFamily: 'Segoe UI', fontSize: `${size}px`, color: '#333333' } }] }] })
  });
  visual.visual.visualContainerObjects.padding = properties({ top: literal(0), bottom: literal(0), left: literal(0), right: literal(0) });
  visual.visual.visualContainerObjects.background = properties({ show: literal(false) });
  writeJson(`definition/pages/${pageId}/visuals/${visual.name}/visual.json`, visual);
}

addText('heading', [32, 24, 1856, 64], 'ZAVA  /  Assign Business Context', 32);
addText('disclosure', [32, 88, 1856, 32], 'Demo context only. Assignments do not change billed cost or write native resource tags.', 18);

addVisual('eligible-candidates', 'tableEx', [32, 136, 1184, 848], 'Eligible, currently unassigned resources', {
  Values: { projections: [
    projection('Business Context Candidates', 'Display Name'),
    projection('Business Context Candidates', 'Platform'),
    projection('Business Context Candidates', 'Service'),
    projection('Business Context Candidates', 'Resource Group'),
    projection('Business Context Candidates', 'Source Attribution')
  ] }
}, {
  columnHeaders: properties({ autoSizeColumnWidth: literal(true), columnAdjustment: literal('growToFit') })
});

addVisual('context-option', 'slicer', [1248, 136, 640, 88], 'Controlled context option', {
  Values: { projections: [projection('Business Context Options', 'Display Label')] }
}, {
  data: properties({ mode: literal('Dropdown') }),
  header: properties({ show: literal(true), text: literal('Business context option') })
});

addText('effective-date-label', [1248, 248, 640, 32], 'Effective date (YYYY-MM-DD)', 14);
const inputQuery = { Values: { projections: [projection('Business Context Options', 'Display Label')] } };
addVisual('effective-date-input', 'textSlicer', [1248, 280, 640, 96], '', inputQuery, {
  inputTextBox: properties({ placeholder: literal('YYYY-MM-DD'), placeholderFontSize: literal(12) })
});

addText('reason-label', [1248, 392, 640, 32], 'Reason (10-1,000 characters)', 14);
addVisual('reason-input', 'textSlicer', [1248, 424, 640, 168], '', inputQuery, {
  inputTextBox: properties({ placeholder: literal('Business justification'), placeholderFontSize: literal(12) })
});

addVisual('assign-context', 'actionButton', [1248, 616, 640, 96], '', null, {
  text: properties({ show: literal(true), text: literal('Assign context'), fontSize: literal(16), bold: literal(true) })
});

addText('assignment-state', [1248, 736, 640, 160], 'A successful assignment is audited and effective-dated. The Import report reflects it after refresh.', 16);
addText('footer', [32, 1008, 1856, 48], 'Fictional demo options are marked [DEMO]. The function validates the selected resource, grant, and version server-side.', 14);

for (const entry of readdirSync(visualRoot, { withFileTypes: true })) {
  if (!entry.isDirectory()) continue;
  const visual = readJson(`definition/pages/${pageId}/visuals/${entry.name}/visual.json`);
  const { x, y, width, height } = visual.position;
  assert.ok([x, y, width, height].every(value => value % 8 === 0), `Off-grid position: ${entry.name}`);
  assert.ok(x >= 0 && y >= 0 && x + width <= 1920 && y + height <= 1080, `Out-of-bounds visual: ${entry.name}`);
}

console.log(JSON.stringify({ report: outputReport, pageId, semanticModelId: modelId, visualCount: readdirSync(visualRoot).length }, null, 2));