import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import assert from 'node:assert/strict';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const report = 'ZavaFinOps.Report';
const schema = 'https://developer.microsoft.com/json-schemas/fabric';
const hash = value => createHash('sha256').update(value).digest('hex').slice(0, 20);
const write = (path, value) => {
  const target = join(root, path);
  mkdirSync(dirname(target), { recursive: true });
  writeFileSync(target, JSON.stringify(value, null, 2) + '\n');
};
const literal = value => ({ expr: { Literal: { Value: typeof value === 'string' ? `'${value.replaceAll("'", "''")}'` : typeof value === 'boolean' ? String(value) : `${value}D` } } });
const color = value => ({ solid: { color: literal(value) } });
const properties = values => [{ properties: values }];
const instance = values => [{ properties: values, selector: { id: 'default' } }];
const field = (table, name, measure = false) => ({ [measure ? 'Measure' : 'Column']: { Expression: { SourceRef: { Entity: table } }, Property: name } });
const projection = (table, name, measure = false) => ({ field: field(table, name, measure), queryRef: `${table}.${name}`, nativeQueryRef: name });
const column = (table, name) => projection(table, name);
const metric = (name, table = 'Billing') => projection(table, name, true);
const role = projections => ({ projections });
const padding = amount => properties(Object.fromEntries(['top', 'bottom', 'left', 'right'].map(side => [side, literal(amount)])));
const palette = { fabric: '#00786B', databricks: '#B94A32', purview: '#006DAA', total: '#242424', other: '#606060' };
const theme = {
  $schema: 'https://raw.githubusercontent.com/microsoft/powerbi-desktop-samples/main/Report%20Theme%20JSON%20Schema/reportThemeSchema-2.153.json',
  dataColors: [palette.fabric, palette.databricks, palette.purview, palette.other],
  foreground: '#242424', background: '#FFFFFF', tableAccent: palette.fabric,
  good: '#00786B', neutral: '#936B00', bad: '#B42318',
  textClasses: {
    callout: { fontSize: 28, fontFace: 'Segoe UI Semibold', color: '#242424' },
    title: { fontSize: 14, fontFace: 'Segoe UI Semibold', color: '#242424' },
    header: { fontSize: 12, fontFace: 'Segoe UI Semibold', color: '#242424' },
    label: { fontSize: 11, fontFace: 'Segoe UI', color: '#444444' }
  },
  visualStyles: {
    '*': { '*': { border: [{ show: false }], dropShadow: [{ show: false }], padding: [{ top: 8, bottom: 8, left: 8, right: 8 }], visualHeader: [{ show: true }] } },
    textbox: { '*': { padding: [{ top: 0, bottom: 0, left: 0, right: 0 }], border: [{ show: false }], background: [{ show: false }] } },
    cardVisual: { '*': { title: [{ show: false }], padding: [{ paddingUniform: 0, $id: 'default' }], value: [{ bold: true, $id: 'default' }], label: [{ show: true, $id: 'default' }], cardCalloutArea: [{ paddingUniform: 0 }], spacing: [{ verticalSpacing: 0, $id: 'default' }] } },
    slicer: { '*': { header: [{ textSize: 12, fontColor: { solid: { color: '#242424' } } }], items: [{ textSize: 12, padding: 4 }] } }
  }
};
for (const type of ['tableEx', 'pivotTable']) {
  theme.visualStyles[type] = { '*': {
    columnHeaders: [{ autoSizeColumnWidth: true, columnAdjustment: 'growToFit', fontColor: { solid: { color: '#242424' } }, backColor: { solid: { color: '#EDF2F1' } }, bold: true, fontSize: 12 }],
    values: [{ fontSize: 12, backColorPrimary: { solid: { color: '#FFFFFF' } }, backColorSecondary: { solid: { color: '#F4F6F5' } }, fontColorPrimary: { solid: { color: '#242424' } }, fontColorSecondary: { solid: { color: '#242424' } } }]
  } };
}
const themeFile = `ZavaLedger-${hash(JSON.stringify(theme)).slice(0, 10)}.json`;
theme.name = themeFile;
const reportMetadata = join(root, report, 'definition/report.json');
if (existsSync(reportMetadata)) {
  const previousTheme = JSON.parse(readFileSync(reportMetadata, 'utf8')).themeCollection.customTheme.name;
  if (previousTheme !== themeFile) {
    renameSync(join(root, report, 'StaticResources/RegisteredResources', previousTheme), join(root, report, 'StaticResources/RegisteredResources', themeFile));
  }
}
write(`${report}/StaticResources/RegisteredResources/${themeFile}`, theme);
write('ZavaFinOps.pbip', { $schema: `${schema}/pbip/pbipProperties/1.0.0/schema.json`, version: '1.0', artifacts: [{ report: { path: report } }], settings: { enableAutoRecovery: true } });
write('ZavaFinOps.SemanticModel/definition.pbism', { $schema: `${schema}/item/semanticModel/definitionProperties/1.0.0/schema.json`, version: '4.2', settings: { qnaEnabled: false } });
write(`${report}/definition.pbir`, { $schema: `${schema}/item/report/definitionProperties/2.0.0/schema.json`, version: '4.0', datasetReference: { byPath: { path: '../ZavaFinOps.SemanticModel' } } });
for (const [folder, type, logicalId] of [[report, 'Report', 'd4d24929-2eb2-4714-adc8-c06f713fc7ec'], ['ZavaFinOps.SemanticModel', 'SemanticModel', 'eed5dfe1-469c-433e-8515-0c6d0da0cfea']]) {
  write(`${folder}/.platform`, { $schema: 'https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json', metadata: { type, displayName: 'Zava FinOps' }, config: { version: '2.0', logicalId } });
}
write(`${report}/definition/version.json`, { $schema: `${schema}/item/report/definition/versionMetadata/1.0.0/schema.json`, version: '2.0.0' });
write(`${report}/definition/report.json`, {
  $schema: `${schema}/item/report/definition/report/3.3.0/schema.json`,
  themeCollection: { customTheme: { name: themeFile, reportVersionAtImport: { visual: '2.9.0', report: '3.3.0', page: '2.1.0' }, type: 'RegisteredResources' } },
  resourcePackages: [{ name: 'RegisteredResources', type: 'RegisteredResources', items: [{ name: themeFile, path: themeFile, type: 'CustomTheme' }] }]
});
const pages = [
  { name: hash('consolidated'), displayName: 'Consolidated Billing', visuals: [] },
  { name: hash('detail'), displayName: 'Billing Detail', visuals: [] }
];
function visual(page, key, type, rect, title, queryState, objects = {}) {
  const order = page.visuals.length * 1000;
  const value = {
    $schema: `${schema}/item/report/definition/visualContainer/2.9.0/schema.json`, name: hash(`${page.name}:${key}`),
    position: { x: rect[0], y: rect[1], width: rect[2], height: rect[3], z: order, tabOrder: order },
    visual: { visualType: type, objects, visualContainerObjects: {
      title: properties({ show: literal(Boolean(title)), text: literal(title), fontSize: literal(14) }),
      general: properties({ altText: literal(title || key) }),
      padding: padding(8), border: properties({ show: literal(false) }),
      background: properties({ show: literal(true), color: color('#FFFFFF'), transparency: literal(0) })
    } }
  };
  if (queryState) value.visual.query = { queryState };
  page.visuals.push(value);
  return value;
}
function text(page, key, rect, content, size = 16) {
  const value = visual(page, key, 'textbox', rect, '', undefined, {
    general: properties({ paragraphs: [{ textRuns: [{ value: content, textStyle: { fontFamily: 'Segoe UI', fontSize: `${size}px`, color: '#333333' } }] }] })
  });
  value.visual.visualContainerObjects.padding = padding(0);
  value.visual.visualContainerObjects.background = properties({ show: literal(false) });
  return value;
}
function slicer(page, table, name, rect) {
  const value = visual(page, `slicer-${name}`, 'slicer', rect, '', { Values: role([column(table, name)]) }, {
    data: properties({ mode: literal('Dropdown') }), header: properties({ show: literal(true), text: literal(name) })
  });
  value.visual.syncGroup = { groupName: `Zava-${name}`, fieldChanges: true, filterChanges: true };
}
function card(page, key, rect, measure, label, accent) {
  assert.ok(16 + Math.ceil(28 * 1.5) + Math.ceil(12 * 1.5) <= rect[3]);
  assert.ok(16 + 12 * 28 * 0.7 <= rect[2]);
  return visual(page, key, 'cardVisual', rect, '', { Data: role([metric(measure)]) }, {
    outline: instance({ show: literal(false) }),
    padding: instance({ paddingUniform: literal(8) }), layout: instance({ paddingUniform: literal(0) }),
    value: instance({ fontSize: literal(28), fontColor: color(accent) }),
    label: instance({ show: literal(true), text: literal(label), fontSize: literal(12) }),
    spacing: instance({ verticalSpacing: literal(0) })
  });
}
const audit = JSON.parse(readFileSync(join(root, '.data/processed/source-audit.json'), 'utf8'));
for (const page of pages) {
  text(page, 'heading', [32, 24, 1856, 64], `ZAVA  /  ${page.displayName}`, 32);
  text(page, 'disclosure', [32, 88, 1856, 32], 'Real Azure billing. Fictional Zava ownership mappings. Resource-level demo attribution only; not chargeback.', 18);
  slicer(page, 'Snapshot', 'Period', [32, 128, 600, 88]);
  slicer(page, 'Context', 'Business Unit', [656, 128, 608, 88]);
  slicer(page, 'Context', 'Initiative', [1288, 128, 600, 88]);
  text(page, 'coverage', [32, 1008, 1856, 48], 'June-August 2026 + September provisional MTD | Latest export: 28 Sep 2026 UTC | USD\nFoundry and M365 billing not observed. Databricks totals exclude unverified supporting infrastructure.', 16);
}
const overview = pages[0];
card(overview, 'direct', [32, 240, 456, 120], 'Direct Platforms USD', 'Three direct platforms | USD', palette.total);
card(overview, 'fabric', [512, 240, 440, 120], 'Fabric USD', 'Fabric | USD', palette.fabric);
card(overview, 'databricks', [976, 240, 440, 120], 'Databricks USD', 'Databricks direct service | USD', palette.databricks);
card(overview, 'purview', [1440, 240, 448, 120], 'Purview USD', 'Purview | USD', palette.purview);
const chart = visual(overview, 'monthly', 'columnChart', [32, 384, 920, 320], 'Direct platform billing by month | September is partial', {
  Category: role([column('Snapshot', 'Period')]), Y: role(['Fabric USD', 'Databricks USD', 'Purview USD'].map(name => metric(name)))
}, {
  categoryAxis: properties({ showAxisTitle: literal(false), fontSize: literal(12) }),
  valueAxis: properties({ start: literal(0), showAxisTitle: literal(false) }),
  legend: properties({ show: literal(true), fontSize: literal(11) }),
  labels: properties({ show: literal(false) }),
  dataPoint: ['Fabric USD', 'Databricks USD', 'Purview USD'].map((name, index) => ({ properties: { fill: color(Object.values(palette)[index]) }, selector: { metadata: `Billing.${name}` } }))
});
chart.visual.query.sortDefinition = { sort: [{ field: field('Snapshot', 'Period'), direction: 'Ascending' }] };
visual(overview, 'initiative', 'tableEx', [976, 384, 912, 320], 'Initiatives connect platforms | exact USD comparison', {
  Values: role([column('Context', 'Initiative'), metric('Fabric USD'), metric('Databricks USD'), metric('Purview USD'), metric('Other USD')])
});
visual(overview, 'ownership', 'tableEx', [32, 728, 1856, 256], 'Business context | fictional mapping, with unassigned costs retained', {
  Values: role(['Business Unit', 'Initiative', 'Project', 'Tool', 'Platform', 'Attribution'].map(name => column('Context', name)).concat(metric('Billed USD')))
});
const detail = pages[1];
visual(detail, 'charges', 'pivotTable', [32, 240, 1856, 440], 'Billing drill-down | Resource > Meter > Charge Category > Charge Class', {
  Rows: role([column('Context', 'Resource'), column('Billing', 'Meter'), column('Billing', 'Charge Category'), column('Billing', 'Charge Class')]),
  Values: role([metric('Billed USD'), metric('Billing Rows'), metric('Negative Charges USD')])
});
visual(detail, 'reconciliation', 'tableEx', [32, 704, 1856, 280], 'Source reconciliation | entire subscription; period filter only, ignores BU and initiative', {
  Values: role([column('Snapshot', 'Period'), column('Snapshot', 'Status'), metric('Source USD', 'Snapshot'), metric('Rounding USD', 'Snapshot'), metric('Model USD', 'Snapshot'), metric('Unexplained USD', 'Snapshot'), metric('Source Row Count', 'Snapshot'), column('Snapshot', 'Submitted UTC')])
});
write(`${report}/definition/pages/pages.json`, { $schema: `${schema}/item/report/definition/pagesMetadata/1.0.0/schema.json`, pageOrder: pages.map(page => page.name), activePageName: overview.name });
for (const page of pages) {
  const path = `${report}/definition/pages/${page.name}`;
  write(`${path}/page.json`, { $schema: `${schema}/item/report/definition/page/2.1.0/schema.json`, name: page.name, displayName: page.displayName, displayOption: 'FitToPage', width: 1920, height: 1080, objects: { background: properties({ color: color('#F4F6F5'), transparency: literal(0) }) } });
  for (const value of page.visuals) {
    const { x, y, width, height } = value.position;
    assert.ok([x, y, width, height].every(number => number % 8 === 0));
    assert.ok(x >= 0 && y >= 0 && x + width <= 1920 && y + height <= 1080);
    for (const other of page.visuals) {
      if (value === other) continue;
      const position = other.position;
      assert.ok(!(x < position.x + position.width && x + width > position.x && y < position.y + position.height && y + height > position.y), `Overlap: ${value.name} / ${other.name}`);
    }
    write(`${path}/visuals/${value.name}/visual.json`, value);
  }
}
console.log(JSON.stringify({ report, pages: pages.map(page => ({ name: page.name, displayName: page.displayName, visuals: page.visuals.length })), auditPeriods: Object.keys(audit).length, layoutChecks: 'passed' }, null, 2));