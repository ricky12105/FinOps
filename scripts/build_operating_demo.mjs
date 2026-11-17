/**
 * PBIR Report Generator for `Zava FinOps Operating Demo`.
 *
 * Reads the approved design contract from `_brief/report-spec.md` and environment/customer
 * configuration from `config/customer.template.json` (plus optional `--config <path>` or
 * environment overrides), generates all 8 report pages (118 visuals) and 2 action-mode
 * bookmarks, and writes the complete PBIR definition to `cloud-dev/ZavaFinOpsOperatingDemo.Report`.
 */
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { cpSync, existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const source = join(root, 'ZavaFinOps.Report');
const target = join(root, 'cloud-dev', 'ZavaFinOpsOperatingDemo.Report');
const python = process.env.PYTHON_EXECUTABLE || 'python';
assert.ok(!existsSync(target) || process.argv.includes('--overwrite'), 'New demo report already exists; rerun with --overwrite to regenerate.');

/**
 * Read and parse a JSON file relative to `folder`.
 * @param {string} folder
 * @param {string} path
 * @returns {any}
 */
const read = (folder, path) => JSON.parse(readFileSync(join(folder, path), 'utf8'));

/**
 * Write `value` as formatted JSON to `join(folder, path)`.
 * @param {string} folder
 * @param {string} path
 * @param {any} value
 */
const write = (folder, path, value) => {
  const destination = join(folder, path);
  mkdirSync(dirname(destination), { recursive: true });
  writeFileSync(destination, `${JSON.stringify(value, null, 2)}\n`);
};

/**
 * Load customer configuration from `config/customer.template.json`, optional `--config <file>`,
 * and environment variables.
 */
const loadCustomerConfig = () => {
  const cfg = existsSync(join(root, 'config', 'customer.template.json'))
    ? read(root, 'config/customer.template.json')
    : {};
  const cfgFlagIdx = process.argv.indexOf('--config');
  if (cfgFlagIdx !== -1 && process.argv[cfgFlagIdx + 1]) {
    const customPath = resolve(process.argv[cfgFlagIdx + 1]);
    if (existsSync(customPath)) {
      const override = JSON.parse(readFileSync(customPath, 'utf8'));
      Object.assign(cfg, override, {
        fabric: { ...(cfg.fabric || {}), ...(override.fabric || {}) },
        databricks: { ...(cfg.databricks || {}), ...(override.databricks || {}) },
        governance: { ...(cfg.governance || {}), ...(override.governance || {}) }
      });
    }
  }
  const fabric = cfg.fabric || {};
  const databricks = cfg.databricks || {};
  const governance = cfg.governance || {};
  return {
    tenantDomain: process.env.AZURE_TENANT_DOMAIN || cfg.tenantDomain || 'contoso.onmicrosoft.com',
    workspaceId: process.env.FABRIC_WORKSPACE_ID || fabric.workspaceId || '00000000-0000-0000-0000-000000000003',
    workspaceName: process.env.FABRIC_WORKSPACE_NAME || fabric.workspaceName || 'FinOps',
    semanticModelId: process.env.FABRIC_SEMANTIC_MODEL_ID || fabric.semanticModelId || '00000000-0000-0000-0000-000000000008',
    semanticModelName: process.env.FABRIC_SEMANTIC_MODEL_NAME || fabric.semanticModelName || 'ZavaFinOpsDemo',
    dataAgentId: process.env.FABRIC_DATA_AGENT_ID || fabric.dataAgentId || '00000000-0000-0000-0000-000000000010',
    databricksWorkspaceId: process.env.DATABRICKS_WORKSPACE_ID || databricks.sourceWorkspaceId || '0000000000000000',
    flowEnvironmentId: process.env.POWER_AUTOMATE_ENVIRONMENT_ID || governance.powerAutomateEnvironmentId || '00000000-0000-0000-0000-000000000011',
    flowId: process.env.POWER_AUTOMATE_FLOW_ID || governance.powerAutomateFlowId || '00000000-0000-0000-0000-000000000012',
    flowLocation: process.env.POWER_AUTOMATE_LOCATION || governance.environmentLocation || 'unitedstates',
    flowRegion: process.env.POWER_AUTOMATE_REGION || governance.environmentRegion || 'westus'
  };
};
const customerCfg = loadCustomerConfig();
const specificationPath = join(root, '_brief', 'report-spec.md');
if (!existsSync(specificationPath)) {
  const existingPages = read(target, 'definition/pages/pages.json');
  assert.equal(existingPages.pageOrder.length, 8, 'A complete prebuilt PBIR report is required when the design brief is unavailable.');
  const deployment = read(root, 'config/demo-serving-deployment.json');
  const modelName = customerCfg.semanticModelName || deployment.semanticModelName;
  const modelId = customerCfg.semanticModelId || deployment.semanticModelId;
  const connectionString = `Data Source="powerbi://api.powerbi.com/v1.0/${customerCfg.tenantDomain}/${customerCfg.workspaceName}";initial catalog=${modelName};access mode=readonly;integrated security=ClaimsToken;semanticmodelid=${modelId}`;
  const datasetReference = modelId ? { byConnection: { connectionString } } : { byPath: { path: '../ZavaFinOpsDemo.SemanticModel' } };
  write(target, 'definition.pbir', { ...read(target, 'definition.pbir'), datasetReference });
  const pages = existingPages.pageOrder.map(pageId => {
    const page = read(target, `definition/pages/${pageId}/page.json`);
    const visualsPath = join(target, 'definition', 'pages', pageId, 'visuals');
    assert.ok(existsSync(visualsPath), `Missing visuals for prebuilt report page ${pageId}.`);
    return { name: page.displayName, pageId, visuals: readdirSync(visualsPath).filter(name => name.endsWith('.json')).length };
  });
  const bookmarks = read(target, 'definition/bookmarks/bookmarks.json').items;
  const status = { report: target, pages, bookmarks, pending: ['Publish report to Fabric and bind Power Automate cloud flow trigger in Service UI', 'Grounded data-agent qualification'] };
  if (!deployment.servingReceiptVerified) status.pending.unshift('Serving materialization awaiting verification');
  if (!modelId) status.pending.unshift('Published model refresh and live DAX tests');
  write(join(root, 'artifacts'), 'operating-demo-build.json', status);
  console.log(JSON.stringify(status, null, 2));
} else {
  const specification = readFileSync(specificationPath, 'utf8');
  const yaml = specification.match(/```yaml\r?\n([\s\S]*?)```/)[1];
  const brief = JSON.parse(execFileSync(python, ['-c', 'import sys,json,yaml; print(json.dumps(yaml.safe_load(sys.stdin.read())["Design Brief"]))'], { input: yaml, encoding: 'utf8' }));
  assert.equal(brief.approval_status, 'approved');
  assert.equal(brief.pages.length, 4);
const dataAgentWebUrl = `https://app.fabric.microsoft.com/groups/${customerCfg.workspaceId}/aiskills/${customerCfg.dataAgentId}`;

/**
 * Generate a deterministic 20-character hex visual/page/bookmark identifier.
 * @param {string} key
 * @returns {string}
 */
const identifier = key => createHash('sha256').update(`zava-operating-demo:${key}`).digest('hex').slice(0, 20);
const sourcePages = read(source, 'definition/pages/pages.json');
const pageSchema = read(source, `definition/pages/${sourcePages.pageOrder[0]}/page.json`).$schema;
const sourceVisuals = join(source, 'definition', 'pages', sourcePages.pageOrder[0], 'visuals');
const visualSchema = read(sourceVisuals, `${readdirSync(sourceVisuals)[0]}/visual.json`).$schema;
const literal = value => ({ expr: { Literal: { Value: typeof value === 'string' ? `'${value.replaceAll("'", "''")}'` : typeof value === 'number' ? `${value}D` : String(value) } } });
const solid = color => ({ solid: { color: literal(color) } });
const properties = value => [{ properties: value }];
const instance = (value, id = 'default') => [{ selector: { id }, properties: value }];
const dual = (value, id = 'default') => [{ properties: value }, { selector: { id }, properties: value }];
const snap = value => Math.round(value / 8) * 8;
const measureNames = new Set([
  'FileShare[Unused]', 'Billed USD', 'Month Change USD', 'Unassigned Share', 'Matched Months', 'Financial Evidence Label', 'Observed Usage', 'Operations', 'Failure Rate',
  'Attributed Usage Share', 'Evidence Label', 'Observation Label', 'Forecast USD', 'Scenario USD', 'Scenario Change USD', 'Actual USD',
  'Backtest Forecast USD', 'Naive USD', 'Absolute Error USD', 'Forecast MAE USD', 'Naive MAE USD', 'Selected Resource Key', 'Selected Resource Version', 'Selected Context Option ID',
  'Selected Resource ID', 'Selected Tag Key', 'Selected Proposed Value', 'NativeTagRequest.Confirmation',
  'UC Schemas', 'UC Tables', 'UC Catalogs', 'UC Volumes', 'UC Assets', 'DBUs', 'Queries', 'Failed Queries', 'Query Failure Rate',
  'Observed CUs', 'Fabric Operation Count', 'Failed Fabric Operations', 'Throttling Minutes', 'Fabric Workspaces', 'Fabric Active Items', 'Fabric Capacities', 'Fabric Item Types',
  'Financial Rows', 'Unassigned USD', 'Negative Charges USD', 'Source USD', 'Model USD', 'Rounding USD', 'Unexplained USD',
  'Anomaly Window Cost USD', 'Expected Baseline USD', 'Anomaly Delta USD', 'Detected Anomalies', 'Evaluated Resource Groups',
  'Active Insights & Alerts', 'High Severity Insights', 'Advisor Recommendations', 'Quantified Impact USD',
  'Foundry Cost USD', 'Foundry Accounts', 'Foundry AI Meters', 'Foundry Resource Groups', 'Foundry Daily Meter Rows', 'Foundry Models USD', 'Foundry Tools USD'
]);
const aliases = {
  'ForecastBacktest[Forecast USD]': 'ForecastBacktest[Backtest Forecast USD]',
  'ForecastBacktest[Mean Absolute Error USD]': 'Forecast Metrics[Forecast MAE USD]',
  'ForecastBacktest[Naive Mean Absolute Error USD]': 'Forecast Metrics[Naive MAE USD]'
};
const colorByQueryRef = {
  'Financial.Billed USD': '#242424',
  'Financial.Month Change USD': '#006DAA',
  'Financial.Unassigned Share': '#8A5700',
  'Reconciliation.Matched Months': '#00786B',
  'Operational View.Observed Usage': '#00786B',
  'Operational View.Operations': '#006DAA',
  'Operational View.Failure Rate': '#B32324',
  'Operational View.Attributed Usage Share': '#8A5700',
  'Forecast.Forecast USD': '#006DAA',
  'Forecast.Scenario USD': '#8A5700',
  'Forecast.Scenario Change USD': '#8A5700',
  'Forecast Metrics.Forecast MAE USD': '#006DAA',
  'Forecast Metrics.Naive MAE USD': '#595959',
  'ForecastBacktest.Actual USD': '#242424',
  'ForecastBacktest.Backtest Forecast USD': '#006DAA',
  'ForecastBacktest.Absolute Error USD': '#B32324'
};
const cardLabels = {
  billed: 'Selected period | USD',
  change: 'Latest vs prior closed month',
  unassigned: 'Selected scope | net cost',
  reconciled: 'Native query within $0.0001',
  usage_metric: 'DBUs or reported CUs | never USD',
  operation_count: 'Statements or FUAM operations',
  outcome_rate: 'Failed / observed count',
  attribution_coverage: 'Linked vs unallocated DBUs',
  forecast_value: 'Oct-Dec 2026 baseline',
  scenario_delta: 'Scenario vs baseline | sensitivity',
  forecast_error: '3 held-out months (Jul-Sep)',
  baseline_error: 'Last-month naive on Jul-Sep'
};
const visualTitles = {
  spend_trend: 'Twelve-month billed cost trend (USD)',
  service_drivers: 'Top services by billed cost (USD)',
  trust_table: 'Row-level charge ledger (30,078 records: date, resource, service, category, cost)',
  attribution_table: 'Resource-level attribution & billed cost (all 854 resources)',
  usage_trend: 'Observed daily usage in active source window',
  outcome_bars: 'Observed outcomes in active source window',
  entity_detail: 'Entity usage, operations and failure rate (no fact join)',
  source_table: 'Row-level operational ledger (daily entities, operations, failures, usage)',
  resource_queue: 'Complete resource inventory (all 854 billed resources — select any resource)',
  audit_history: 'Action audit trail (caller vs execution identity)',
  tag_preview: 'Resource tags (select a row or manually edit Tag Key & Value below)',
  forecast_trend: 'Historical actuals vs modeled baseline and scenario (USD)',
  scenario_values: 'Three-month modeled horizon by scenario',
  heldout_results: 'Retrospective rolling-origin backtest (Jul-Sep 2026)',
  method_notes: 'Forecast methodology, range basis and limitations'
};
function binding(reference) {
  reference = aliases[reference] || reference;
  const [, table, column] = reference.match(/^(.+)\[([^\]]+)\]$/);
  const measure = measureNames.has(column) || measureNames.has(`${table}.${column}`);
  return {
    field: { [measure ? 'Measure' : 'Column']: { Expression: { SourceRef: { Entity: table } }, Property: column } },
    queryRef: `${table}.${column}`, nativeQueryRef: column
  };
}
function bounds(page, region, slot = 1, of = 1) {
  const [left, top, right, bottom] = page.layout_contract.grid.regions[region];
  const stepX = (1920 - 64 + 24) / 12;
  const stepY = (1080 - 64 + 24) / 12;
  const area = [snap(32 + (left - 1) * stepX), snap(32 + (top - 1) * stepY), snap((right - left) * stepX - 24), snap((bottom - top) * stepY - 24)];
  if (of === 1) return area;
  if (region === 'header') {
    const offsets = [0, 48, 96];
    return [area[0], area[1] + offsets[slot - 1], area[2], slot === 3 ? 48 : 40];
  }
  if (region === 'action') {
    const step = (area[3] + 24) / of;
    return [area[0], snap(area[1] + (slot - 1) * step), area[2], snap(step - 24)];
  }
  const step = (area[2] + 24) / of;
  return [snap(area[0] + (slot - 1) * step), area[1], snap(step - 24), area[3]];
}

if (existsSync(join(target, 'definition'))) rmSync(join(target, 'definition'), { recursive: true, force: true });
mkdirSync(target, { recursive: true });
cpSync(join(source, 'StaticResources'), join(target, 'StaticResources'), { recursive: true });
write(target, 'definition/report.json', read(source, 'definition/report.json'));
write(target, 'definition/version.json', read(source, 'definition/version.json'));
const deployment = read(root, 'config/demo-serving-deployment.json');
const modelName = customerCfg.semanticModelName || deployment.semanticModelName;
const modelId = customerCfg.semanticModelId || deployment.semanticModelId;
const connectionString = `Data Source="powerbi://api.powerbi.com/v1.0/${customerCfg.tenantDomain}/${customerCfg.workspaceName}";initial catalog=${modelName};access mode=readonly;integrated security=ClaimsToken;semanticmodelid=${modelId}`;
const datasetReference = modelId ? { byConnection: { connectionString } } : { byPath: { path: '../ZavaFinOpsDemo.SemanticModel' } };
write(target, 'definition.pbir', { ...read(source, 'definition.pbir'), datasetReference });
write(target, '.platform', { $schema: 'https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json', metadata: { type: 'Report', displayName: 'Zava FinOps Operating Demo', description: 'Approved four-page operating demo; serving refresh and report-native actions require acceptance.' }, config: { version: '2.0', logicalId: '48dd7814-7068-483e-9466-e1947f3d4fcf' } });
write(join(root, 'cloud-dev'), 'ZavaFinOpsOperatingDemo.pbip', { version: '1.0', artifacts: [{ report: { path: 'ZavaFinOpsOperatingDemo.Report' } }], settings: { enableAutoRecovery: true } });
write(join(root, 'cloud-dev', 'ZavaFinOpsDemo.SemanticModel'), 'definition.pbism', { version: '4.2', settings: {} });

const status = { report: target, pages: [], bookmarks: [], pending: ['Publish report to Fabric and bind Power Automate cloud flow trigger in Service UI', 'Grounded data-agent qualification'] };
if (!deployment.servingReceiptVerified) status.pending.unshift('Serving materialization awaiting verification');
if (!deployment.semanticModelId) status.pending.unshift('Published model refresh and live DAX tests');
for (const page of brief.pages) {
  const pageId = identifier(page.name);
  const prefix = `definition/pages/${pageId}`;
  const visuals = [];
  let order = 0;
  function add(key, type, rectangle, title, queryState, objects = {}, isHidden = false) {
    const name = identifier(`${page.name}:${key}`);
    const [x, y, width, height] = rectangle;
    assert.ok([x, y, width, height].every(value => value % 8 === 0), `Non-8px snapped bounds on ${key}: ${rectangle}`);
    assert.ok(x >= 0 && y >= 0 && x + width <= 1920 && y + height <= 1080, `Out-of-bounds: ${key}`);
    const visual = { $schema: visualSchema, name, position: { x, y, width, height, z: order, tabOrder: order }, ...(isHidden ? { isHidden: true } : {}), visual: {
      visualType: type, objects,
      visualContainerObjects: {
        title: properties({ show: literal(Boolean(title)), text: literal(title || ''), fontSize: literal(13), titleWrap: literal(true) }),
        general: properties({ altText: literal(title || key) }),
        background: properties({ show: literal(true), color: solid('#FFFFFF'), transparency: literal(0) }),
        border: properties({ show: literal(true), color: solid('#E0E4E8'), radius: literal(4), width: literal(1) }),
        padding: properties({ top: literal(8), bottom: literal(8), left: literal(8), right: literal(8) }),
        visualHeader: properties({ show: literal(true) })
      }
    } };
    if (queryState) visual.visual.query = { queryState };
    order += 1000;
    visuals.push(visual);
    return visual;
  }
  function text(key, rectangle, value, size = 14, isHidden = false) {
    const visual = add(key, 'textbox', rectangle, '', null, { general: properties({ paragraphs: [{ textRuns: [{ value, textStyle: { fontFamily: 'Segoe UI', fontSize: `${size}pt`, color: '#242424' } }] }] }) }, isHidden);
    visual.visual.visualContainerObjects.background = properties({ show: literal(false) });
    visual.visual.visualContainerObjects.border = properties({ show: literal(false), radius: literal(0) });
    visual.visual.visualContainerObjects.padding = properties({ top: literal(0), bottom: literal(0), left: literal(0), right: literal(0) });
    return visual;
  }
  for (const placement of page.layout_contract.placements) {
    if (page.name === 'Governance & Action' && ['action_mode', 'tag_preview', 'confirm_tags', 'apply_tags', 'context_form'].includes(placement.id)) continue;
    const rect = bounds(page, placement.region, placement.slot, placement.of);
    let type = placement.kind;
    if (type === 'textbox' && placement.text) {
      text(placement.id, rect, placement.text, 22);
      continue;
    }
    if (placement.id === 'evidence_strip') {
      const label = {
        'Overview & Trust': '12 closed months (Oct 2025-Sep 2026) | 30,078 row-level charges & 854 resources | exact-source bridge retained',
        'Usage & Investigation': 'Databricks (May-Jun DBUs & statements) or Fabric FUAM (Aug 17-28 CUs) | row-level daily operational ledger',
        'Governance & Action': 'All 854 billed resources | user-editable Tag Key & Proposed Value | mutually exclusive action modes',
        'Planning': 'History through Sep 2026 | Oct-Dec modeled horizon | retrospective holdouts, not vintage replay'
      }[page.name];
      text(placement.id, rect, label, 11);
      continue;
    }
    if (type === 'pageNavigator') {
      add(placement.id, type, rect, '', null, {
        text: dual({ show: literal(true), fontSize: literal(11), fontFamily: literal('Segoe UI'), fontColor: solid('#242424') }),
        fill: dual({ show: literal(true), fillColor: solid('#EDF2F1'), transparency: literal(0) })
      });
      if (['Overview & Trust', 'Planning'].includes(page.name)) {
        const agentBtn = add('launch_data_agent', 'actionButton', [1152, 128, 736, 48], '', null, {
          text: dual({ show: literal(true), text: literal('Ask ZavaFinOps_Agent (Open Grounded Fabric Data Agent Q&A)'), fontSize: literal(11), bold: literal(true), fontColor: solid('#FFFFFF') }),
          fill: dual({ show: literal(true), fillColor: solid('#006DAA'), transparency: literal(0) })
        });
        agentBtn.visual.visualContainerObjects.visualLink = properties({
          show: literal(true),
          type: literal('WebUrl'),
          webUrl: literal(dataAgentWebUrl),
          tooltip: literal('Open ZavaFinOps_Agent in Fabric to ask grounded natural-language questions across Financials, Databricks UC, Fabric FUAM, Tag Governance, and Forecasting')
        });
      }
      continue;
    }
    if (type === 'cardVisual') {
      const proj = binding(placement.field_bindings);
      const valColor = colorByQueryRef[proj.queryRef] || '#242424';
      const subLabel = cardLabels[placement.id] || placement.context || '';
      const visual = add(placement.id, type, rect, '', { Data: { projections: [proj] } }, {
        outline: instance({ show: literal(false) }),
        padding: instance({ paddingUniform: literal(4) }),
        layout: instance({ paddingUniform: literal(0) }),
        spacing: instance({ verticalSpacing: literal(2) }),
        value: instance({ fontSize: literal(26), fontColor: solid(valColor), labelDisplayUnits: literal('1') }),
        label: instance({ show: literal(true), text: literal(`${proj.nativeQueryRef} — ${subLabel}`), fontSize: literal(10), fontColor: solid('#595959') })
      });
      visual.visual.visualContainerObjects.title = properties({ show: literal(false), text: literal(''), fontSize: literal(12) });
      continue;
    }
    if (type === 'slicer') {
      const strictSingle = ['platform', 'scenario_choice'].includes(placement.id);
      const headerLabels = {
        period: 'Period (multi-select)',
        platform: 'Platform (required single-select)',
        window: 'Observation window',
        entity: 'Entity (all by default)',
        scenario_choice: 'Sensitivity scenario'
      };
      const sRect = [rect[0], rect[1], rect[2], 88];
      const visual = add(placement.id, 'slicer', sRect, '', { Values: { projections: [binding(placement.field_bindings)] } }, {
        data: properties({ mode: literal('Dropdown') }),
        header: properties({ show: literal(true), text: literal(headerLabels[placement.id] || placement.id.replaceAll('_', ' ')) }),
        selection: properties({ strictSingleSelect: literal(strictSingle), selectAllCheckboxEnabled: literal(!strictSingle) })
      });
      if (placement.id === 'platform') {
        const column = { Column: { Expression: { SourceRef: { Source: 'platform' } }, Property: 'Platform' } };
        visual.visual.objects.general = properties({ filter: { filter: { Version: 2, From: [{ Name: 'platform', Entity: 'Operational View', Type: 0 }], Where: [{ Condition: { In: { Expressions: [column], Values: [[{ Literal: { Value: "'Databricks'" } }]] } } }] } } });
      }
      if (placement.id === 'scenario_choice') {
        const column = { Column: { Expression: { SourceRef: { Source: 'scenario' } }, Property: 'Scenario' } };
        visual.visual.objects.general = properties({ filter: { filter: { Version: 2, From: [{ Name: 'scenario', Entity: 'Scenario', Type: 0 }], Where: [{ Condition: { In: { Expressions: [column], Values: [[{ Literal: { Value: "'Baseline'" } }]] } } }] } } });
      }
      continue;
    }
    if (type === 'tableEx') {
      const tblTitle = visualTitles[placement.id] || placement.purpose;
      let fields = placement.field_bindings;
      if (placement.id === 'trust_table') {
        fields = ['Financial[Period]', 'Resource[Resource]', 'Service[Service]', 'Financial[Charge Category]', 'BusinessContext[Business Unit]', 'Financial[Billed USD]', 'Financial[Financial Rows]'];
      } else if (placement.id === 'attribution_table') {
        fields = ['BusinessContext[Business Unit]', 'BusinessContext[Project]', 'Resource[Resource]', 'BusinessContext[Attribution Basis]', 'Financial[Billed USD]', 'Financial[Financial Rows]'];
      } else if (placement.id === 'source_table') {
        fields = ['Calendar[Date]', 'Operational View[Platform]', 'Operational View[Entity]', 'Operational View[Outcome]', 'Operational View[Observed Usage]', 'Operational View[Operations]'];
      } else if (placement.id === 'resource_queue') {
        fields = ['ResourceInventory[Resource]', 'ResourceInventory[Classification Status]', 'ResourceInventory[Eligible Action]', 'ResourceInventory[Resource ID]'];
      }
      add(placement.id, type, rect, tblTitle, { Values: { projections: fields.map(binding) } }, {
        columnHeaders: properties({ autoSizeColumnWidth: literal(true), columnAdjustment: literal('growToFit'), wordWrap: literal(true), fontSize: literal(10) }),
        values: properties({ wordWrap: literal(true), fontSize: literal(10) })
      });
      continue;
    }
    if (type === 'lineChart' || type === 'barChart') {
      const measures = Array.isArray(placement.field_bindings.Y) ? placement.field_bindings.Y : [placement.field_bindings.Y];
      const projections = measures.map(binding);
      const dataPoint = projections.length === 1
        ? [{ properties: { defaultColor: solid(colorByQueryRef[projections[0].queryRef] || '#00786B') } }]
        : projections.map(proj => ({ selector: { metadata: proj.queryRef }, properties: { fill: solid(colorByQueryRef[proj.queryRef] || '#006DAA') } }));
      const chartTitle = visualTitles[placement.id] || placement.purpose;
      const actualType = (type === 'lineChart' && ['spend_trend', 'usage_trend'].includes(placement.id)) ? 'areaChart' : type;
      const chartObjects = { dataPoint };
      if (type === 'lineChart') {
        chartObjects.categoryAxis = properties({ axisType: literal('Categorical'), showAxisTitle: literal(false), fontSize: literal(10) });
      }
      const visual = add(placement.id, actualType, rect, chartTitle, { Category: { projections: [binding(placement.field_bindings.Category)] }, Y: { projections } }, chartObjects);
      visual.visual.query.sortDefinition = { sort: [{ field: binding(placement.sort_policy === 'value_desc' ? measures[0] : placement.field_bindings.Category).field, direction: placement.sort_policy === 'value_desc' ? 'Descending' : 'Ascending' }], isDefaultSort: true };
      continue;
    }
    throw new Error(`Unhandled approved placement: ${placement.id}/${type}`);
  }
  if (page.name === 'Governance & Action') {
    const filterArea = bounds(page, 'filters');
    add('action_mode', 'bookmarkNavigator', [filterArea[0], filterArea[1], filterArea[2], 64], 'Action mode (mutually exclusive)', null, {
      text: dual({ show: literal(true), fontSize: literal(11), fontFamily: literal('Segoe UI Semibold'), fontColor: solid('#242424') }),
      fill: dual({ show: literal(true), fillColor: solid('#EDF2F1'), transparency: literal(0) })
    });
    add('action_mode_slicer', 'slicer', [filterArea[0], filterArea[1] + 72, filterArea[2], 80], '', { Values: { projections: [binding('Action Mode[Mode]')] } }, {
      data: properties({ mode: literal('Dropdown') }),
      header: properties({ show: literal(true), text: literal('Selected action contract') }),
      selection: properties({ strictSingleSelect: literal(true), selectAllCheckboxEnabled: literal(false) }),
      general: properties({ filter: { filter: { Version: 2, From: [{ Name: 'mode', Entity: 'Action Mode', Type: 0 }], Where: [{ Condition: { In: { Expressions: [{ Column: { Expression: { SourceRef: { Source: 'mode' } }, Property: 'Mode' } }], Values: [[{ Literal: { Value: "'Native Azure tags'" } }]] } } }] } } })
    });

    const action = bounds(page, 'action');
    const tagPreview = add('tag_preview', 'tableEx', [action[0], action[1], action[2], 224], visualTitles.tag_preview, {
      Values: { projections: ['NativeTagRequest[Resource ID]', 'NativeTagRequest[Tag]', 'NativeTagRequest[Current Value]', 'NativeTagRequest[Proposed Value]'].map(binding) }
    }, {
      columnHeaders: properties({ autoSizeColumnWidth: literal(true), columnAdjustment: literal('growToFit'), wordWrap: literal(true), fontSize: literal(10) }),
      values: properties({ wordWrap: literal(true), fontSize: literal(10) })
    }, false);

    const halfWidth = snap((action[2] - 16) / 2);
    const tagKeyInput = add('tag_key_input', 'textSlicer', [action[0], action[1] + 240, halfWidth, 96], 'Edit Tag Key (e.g. department, cost-center, owner)', {
      Values: { projections: [binding('NativeTagRequest[Tag]')] }
    }, {
      inputTextBox: properties({ placeholder: literal('Type or filter Tag Key (e.g. cost-center)'), placeholderFontSize: literal(11) })
    }, false);

    const tagValueInput = add('tag_value_input', 'textSlicer', [action[0] + halfWidth + 16, action[1] + 240, action[2] - halfWidth - 16, 96], 'Edit Proposed Tag Value (manual override)', {
      Values: { projections: [binding('NativeTagRequest[Proposed Value]')] }
    }, {
      inputTextBox: properties({ placeholder: literal('Type custom Tag Value (e.g. CC-2050)'), placeholderFontSize: literal(11) })
    }, false);

    const confirmTags = add('confirm_tags', 'slicer', [action[0], action[1] + 352, action[2], 88], '', {
      Values: { projections: [binding('Confirmation[Confirmation]')] }
    }, {
      data: properties({ mode: literal('Dropdown') }),
      header: properties({ show: literal(true), text: literal('Explicit confirmation gate (required before Merge)') }),
      selection: properties({ strictSingleSelect: literal(true), selectAllCheckboxEnabled: literal(false) }),
      general: properties({ filter: { filter: { Version: 2, From: [{ Name: 'confirm', Entity: 'Confirmation', Type: 0 }], Where: [{ Condition: { In: { Expressions: [{ Column: { Expression: { SourceRef: { Source: 'confirm' } }, Property: 'Confirmation' } }], Values: [[{ Literal: { Value: "'Not confirmed'" } }]] } } }] } } })
    }, false);

    const applyTagsPayload = add('apply_tags', 'FlowVisual_C29F1DCC_81F5_4973_94AD_0517D44CC06A', [action[0], action[1] + 456, action[2], 240], 'Apply Edited Tags via Power Automate (passes Resource ID, Tag Key, Proposed Value & Confirmation)', {
      table: { projections: ['NativeTagRequest[Resource ID]', 'NativeTagRequest[Tag]', 'NativeTagRequest[Proposed Value]', 'NativeTagRequest[Proposal ID]', 'NativeTagRequest[Confirmation]'].map(binding) }
    }, {
      flowInfo: properties({
        FlowId: literal(`/providers/Microsoft.Flow/environments/${customerCfg.flowEnvironmentId}/flows/${customerCfg.flowId}`),
        ManagementUri: literal('https://us.api.flow.microsoft.com/providers/Microsoft.ProcessSimple'),
        EnvironmentId: literal(customerCfg.flowEnvironmentId),
        EnvironmentLocation: literal(customerCfg.flowLocation),
        EnvironmentRegion: literal(customerCfg.flowRegion)
      })
    }, false);
    delete applyTagsPayload.visual.objects.columnHeaders;
    delete applyTagsPayload.visual.objects.values;

    const selectedTagSummary = add('selected_tag_summary', 'tableEx', [action[0], action[1] + 712, action[2], 112], 'Active payload summary (Selected Resource, Tag Key, Proposed Value, Confirmation)', {
      Values: { projections: ['NativeTagRequest[Selected Resource ID]', 'NativeTagRequest[Selected Tag Key]', 'NativeTagRequest[Selected Proposed Value]', 'NativeTagRequest[Confirmation]'].map(binding) }
    }, {
      columnHeaders: properties({ autoSizeColumnWidth: literal(true), columnAdjustment: literal('growToFit'), wordWrap: literal(true), fontSize: literal(9) }),
      values: properties({ wordWrap: literal(true), fontSize: literal(9) })
    }, false);

    const contextCandidates = add('context_candidates', 'tableEx', [action[0], action[1], action[2], 248], 'Assignable resources (select single row for UDF)', {
      Values: { projections: ['Business Context Candidates[Resource]', 'Business Context Candidates[Resource Key]', 'Business Context Candidates[Resource Version]'].map(binding) }
    }, {
      columnHeaders: properties({ autoSizeColumnWidth: literal(true), columnAdjustment: literal('growToFit'), wordWrap: literal(true), fontSize: literal(10) }),
      values: properties({ wordWrap: literal(true), fontSize: literal(10) })
    }, true);

    const contextOption = add('context_option', 'slicer', [action[0], action[1] + 264, action[2], 88], '', {
      Values: { projections: [binding('Business Context Options[Project]')] }
    }, {
      data: properties({ mode: literal('Dropdown') }),
      header: properties({ show: literal(true), text: literal('Controlled business context project') }),
      selection: properties({ strictSingleSelect: literal(true), selectAllCheckboxEnabled: literal(false) })
    }, true);

    const contextInputQuery = { Values: { projections: [binding('Business Context Options[Tool]')] } };
    const effectiveDateInput = add('effective_date_input', 'textSlicer', [action[0], action[1] + 368, action[2], 96], 'Effective date (YYYY-MM-DD)', contextInputQuery, {
      inputTextBox: properties({ placeholder: literal('YYYY-MM-DD'), placeholderFontSize: literal(11) })
    }, true);

    const reasonInput = add('reason_input', 'textSlicer', [action[0], action[1] + 480, action[2], 120], 'Assignment justification (10-1,000 chars)', contextInputQuery, {
      inputTextBox: properties({ placeholder: literal('Business justification for effective-dated assignment'), placeholderFontSize: literal(11) })
    }, true);

    const contextForm = add('context_form', 'actionButton', [action[0], action[1] + 616, action[2], 80], 'Translytical Task Flow: AssignBusinessContext UDF', null, {
      text: dual({ show: literal(true), text: literal('Assign Business Context (AssignBusinessContext UDF)'), fontSize: literal(14), bold: literal(true), fontColor: solid('#FFFFFF') }),
      fill: dual({ show: literal(true), fillColor: solid('#006DAA'), transparency: literal(0) })
    }, true);

    const contextPayload = add('context_payload', 'tableEx', [action[0], action[1] + 712, action[2], 112], 'UDF parameters (Selected Resource Key, Version, Option ID)', {
      Values: { projections: ['Business Context Candidates[Selected Resource Key]', 'Business Context Candidates[Selected Resource Version]', 'Business Context Options[Selected Context Option ID]'].map(binding) }
    }, {
      columnHeaders: properties({ autoSizeColumnWidth: literal(true), columnAdjustment: literal('growToFit'), wordWrap: literal(true), fontSize: literal(9) }),
      values: properties({ wordWrap: literal(true), fontSize: literal(9) })
    }, true);

    const nativeVisuals = [tagPreview, tagKeyInput, tagValueInput, confirmTags, applyTagsPayload, selectedTagSummary];
    const contextVisuals = [contextCandidates, contextOption, effectiveDateInput, reasonInput, contextForm, contextPayload];
    const allTargetNames = [...nativeVisuals, ...contextVisuals].map(v => v.name);

    const makeBookmark = (bmKey, displayName, showNative) => {
      const bmName = identifier(`bookmark:${bmKey}`);
      const visualContainers = {};
      for (const v of nativeVisuals) {
        visualContainers[v.name] = { singleVisual: { visualType: v.visual.visualType, ...(showNative ? {} : { display: { mode: 'hidden' } }) } };
      }
      for (const v of contextVisuals) {
        visualContainers[v.name] = { singleVisual: { visualType: v.visual.visualType, ...(showNative ? { display: { mode: 'hidden' } } : {}) } };
      }
      write(target, `definition/bookmarks/${bmName}.bookmark.json`, {
        $schema: 'https://developer.microsoft.com/json-schemas/fabric/item/report/definition/bookmark/1.1.0/schema.json',
        name: bmName,
        displayName,
        options: {
          applyOnlyToTargetVisuals: true,
          targetVisualNames: allTargetNames,
          suppressData: true,
          suppressActiveSection: true
        },
        explorationState: {
          version: '1.3',
          activeSection: pageId,
          sections: {
            [pageId]: { visualContainers }
          }
        }
      });
      return bmName;
    };

    const bmNative = makeBookmark('native-azure-tags', 'Native Azure tags', true);
    const bmContext = makeBookmark('business-context', 'Business context', false);
    write(target, 'definition/bookmarks/bookmarks.json', {
      $schema: 'https://developer.microsoft.com/json-schemas/fabric/item/report/definition/bookmarksMetadata/1.0.0/schema.json',
      items: [{ name: bmNative }, { name: bmContext }]
    });
    status.bookmarks.push(bmNative, bmContext);
  }
  for (const visual of visuals) write(target, `${prefix}/visuals/${visual.name}/visual.json`, visual);
  const visualInteractions = [];
  const scenarioChoice = visuals.find(visual => visual.name === identifier(`${page.name}:scenario_choice`));
  if (scenarioChoice) {
    for (const key of ['heldout_results', 'method_notes']) visualInteractions.push({ source: scenarioChoice.name, target: identifier(`${page.name}:${key}`), type: 'NoFilter' });
  }
  if (page.name === 'Governance & Action') {
    const effInput = identifier(`${page.name}:effective_date_input`);
    const rsnInput = identifier(`${page.name}:reason_input`);
    const optInput = identifier(`${page.name}:context_option`);
    visualInteractions.push(
      { source: effInput, target: optInput, type: 'NoFilter' },
      { source: rsnInput, target: optInput, type: 'NoFilter' },
      { source: effInput, target: rsnInput, type: 'NoFilter' },
      { source: rsnInput, target: effInput, type: 'NoFilter' }
    );
  }
  write(target, `${prefix}/page.json`, { $schema: pageSchema, name: pageId, displayName: page.name, displayOption: 'FitToPage', width: 1920, height: 1080, visualInteractions });
  status.pages.push({ name: page.name, pageId, visuals: visuals.length });
}

{
  const ucPageName = 'Databricks & UC System Tables';
  const ucPageId = identifier(ucPageName);
  const ucPrefix = `definition/pages/${ucPageId}`;
  const ucVisuals = [];
  let ucOrder = 0;
  function addUc(key, type, rectangle, title, queryState, objects = {}) {
    const name = identifier(`${ucPageName}:${key}`);
    const [x, y, width, height] = rectangle;
    assert.ok([x, y, width, height].every(value => value % 8 === 0), `Non-8px snapped bounds on ${key}: ${rectangle}`);
    assert.ok(x >= 0 && y >= 0 && x + width <= 1920 && y + height <= 1080, `Out-of-bounds: ${key}`);
    const visual = { $schema: visualSchema, name, position: { x, y, width, height, z: ucOrder, tabOrder: ucOrder }, visual: {
      visualType: type, objects,
      visualContainerObjects: {
        title: properties({ show: literal(Boolean(title)), text: literal(title || ''), fontSize: literal(13), titleWrap: literal(true) }),
        general: properties({ altText: literal(title || key) }),
        background: properties({ show: literal(true), color: solid('#FFFFFF'), transparency: literal(0) }),
        border: properties({ show: literal(true), color: solid('#E0E4E8'), radius: literal(4), width: literal(1) }),
        padding: properties({ top: literal(8), bottom: literal(8), left: literal(8), right: literal(8) }),
        visualHeader: properties({ show: literal(true) })
      }
    } };
    if (queryState) visual.visual.query = { queryState };
    ucOrder += 1000;
    ucVisuals.push(visual);
    return visual;
  }
  function textUc(key, rectangle, value, size = 14) {
    const visual = addUc(key, 'textbox', rectangle, '', null, { general: properties({ paragraphs: [{ textRuns: [{ value, textStyle: { fontFamily: 'Segoe UI', fontSize: `${size}pt`, color: '#242424' } }] }] }) });
    visual.visual.visualContainerObjects.background = properties({ show: literal(false) });
    visual.visual.visualContainerObjects.border = properties({ show: literal(false), radius: literal(0) });
    visual.visual.visualContainerObjects.padding = properties({ top: literal(0), bottom: literal(0), left: literal(0), right: literal(0) });
    return visual;
  }
  function heroCard(key, rectangle, topTitle, ref, subLabel, color = '#242424') {
    const proj = binding(ref);
    return addUc(key, 'cardVisual', rectangle, topTitle, { Data: { projections: [proj] } }, {
      outline: instance({ show: literal(false) }),
      padding: instance({ paddingUniform: literal(4) }),
      layout: instance({ paddingUniform: literal(0) }),
      spacing: instance({ verticalSpacing: literal(2) }),
      value: instance({ fontSize: literal(34), fontColor: solid(color), labelDisplayUnits: literal('1') }),
      label: instance({ show: literal(true), text: literal(subLabel), fontSize: literal(11), fontColor: solid('#595959') })
    });
  }

  textUc('page_title', [32, 32, 1096, 40], 'Cost Management & Governance (powered by UC System Tables)', 22);
  textUc('evidence_strip', [32, 80, 1096, 40], `Workspace ${customerCfg.databricksWorkspaceId} | system.information_schema + system.billing.usage + system.query.history | live snapshot`, 11);
  addUc('navigation', 'pageNavigator', [32, 128, 1096, 48], '', null, {
    text: dual({ show: literal(true), fontSize: literal(11), fontFamily: literal('Segoe UI'), fontColor: solid('#242424') }),
    fill: dual({ show: literal(true), fillColor: solid('#EDF2F1'), transparency: literal(0) })
  });
  const ucAgentBtn = addUc('launch_data_agent', 'actionButton', [1152, 128, 736, 48], '', null, {
    text: dual({ show: literal(true), text: literal('Ask ZavaFinOps_Agent (Open Grounded Fabric Data Agent Q&A)'), fontSize: literal(11), bold: literal(true), fontColor: solid('#FFFFFF') }),
    fill: dual({ show: literal(true), fillColor: solid('#006DAA'), transparency: literal(0) })
  });
  ucAgentBtn.visual.visualContainerObjects.visualLink = properties({
    show: literal(true),
    type: literal('WebUrl'),
    webUrl: literal(dataAgentWebUrl),
    tooltip: literal('Open ZavaFinOps_Agent in Fabric')
  });

  addUc('catalog_slicer', 'slicer', [1152, 32, 360, 88], '', { Values: { projections: [binding('Unity Catalog Inventory[Catalog]')] } }, {
    data: properties({ mode: literal('Dropdown') }),
    header: properties({ show: literal(true), text: literal('Unity Catalog (filter)') }),
    selection: properties({ strictSingleSelect: literal(false), selectAllCheckboxEnabled: literal(true) })
  });
  addUc('sku_slicer', 'slicer', [1528, 32, 360, 88], '', { Values: { projections: [binding('Databricks Usage[SKU]')] } }, {
    data: properties({ mode: literal('Dropdown') }),
    header: properties({ show: literal(true), text: literal('Databricks Compute SKU') }),
    selection: properties({ strictSingleSelect: literal(false), selectAllCheckboxEnabled: literal(true) })
  });

  heroCard('uc_schemas', [32, 192, 288, 160], 'Schemas', 'Unity Catalog Inventory[UC Schemas]', 'Schemas  |  ⟳ system.information_schema');
  heroCard('uc_tables', [336, 192, 288, 160], 'Tables', 'Unity Catalog Inventory[UC Tables]', 'Tables  |  ⟳ system.information_schema');
  heroCard('uc_catalogs', [640, 192, 288, 160], 'Catalogs', 'Unity Catalog Inventory[UC Catalogs]', 'Catalogs  |  ⟳ system.information_schema');

  const formatCol = addUc('tables_by_format', 'columnChart', [944, 192, 944, 344], 'Tables by format (DELTA, UNITY_CATALOG, DELTASHARING, VIEW)', {
    Category: { projections: [binding('Unity Catalog Inventory[Asset Format]')] },
    Y: { projections: [binding('Unity Catalog Inventory[UC Tables]')] }
  }, {
    dataPoint: [{ properties: { defaultColor: solid('#006DAA') } }],
    labels: properties({ show: literal(true) })
  });
  formatCol.visual.query.sortDefinition = { sort: [{ field: binding('Unity Catalog Inventory[UC Tables]').field, direction: 'Descending' }], isDefaultSort: true };

  addUc('tables_created_mom', 'areaChart', [32, 368, 592, 288], 'Tables Created (MoM) by table_type', {
    Category: { projections: [binding('Unity Catalog Inventory[Month Created]')] },
    Series: { projections: [binding('Unity Catalog Inventory[Asset Type]')] },
    Y: { projections: [binding('Unity Catalog Inventory[UC Tables]')] }
  }, {
    categoryAxis: properties({ axisType: literal('Categorical'), showAxisTitle: literal(false), fontSize: literal(10) }),
    legend: properties({ show: literal(true), position: literal('Bottom'), fontSize: literal(9) })
  });

  addUc('dbu_by_product_donut', 'donutChart', [640, 368, 288, 288], 'DBU Share by Workload Product', {
    Category: { projections: [binding('Databricks Usage[Product]')] },
    Y: { projections: [binding('Databricks Usage[DBUs]')] }
  }, {
    labels: properties({ show: literal(true), labelStyle: literal('Percent of total'), fontSize: literal(10) }),
    legend: properties({ show: literal(true), position: literal('Bottom'), fontSize: literal(9) })
  });

  addUc('dbu_burn_area', 'areaChart', [32, 672, 904, 376], 'Daily DBU Consumption by Compute SKU (system.billing.usage — 504.86 DBUs)', {
    Category: { projections: [binding('Calendar[Date]')] },
    Series: { projections: [binding('Databricks Usage[SKU]')] },
    Y: { projections: [binding('Databricks Usage[DBUs]')] }
  }, {
    categoryAxis: properties({ axisType: literal('Categorical'), showAxisTitle: literal(false), fontSize: literal(9) }),
    legend: properties({ show: literal(true), position: literal('Bottom'), fontSize: literal(9) })
  });

  addUc('tables_by_type_donut', 'donutChart', [952, 552, 456, 496], 'Tables Created by Type (MANAGED, VIEW, EXTERNAL)', {
    Category: { projections: [binding('Unity Catalog Inventory[Asset Type]')] },
    Y: { projections: [binding('Unity Catalog Inventory[UC Tables]')] }
  }, {
    labels: properties({ show: literal(true), labelStyle: literal('Data value, percent of total'), fontSize: literal(10) }),
    legend: properties({ show: literal(true), position: literal('Right'), fontSize: literal(10) })
  });

  addUc('uc_asset_ledger', 'tableEx', [1424, 552, 464, 496], 'Unity Catalog Asset Ledger (262 live objects)', {
    Values: { projections: ['Unity Catalog Inventory[Catalog]', 'Unity Catalog Inventory[Schema]', 'Unity Catalog Inventory[Asset Name]', 'Unity Catalog Inventory[Asset Type]', 'Unity Catalog Inventory[Asset Format]'].map(binding) }
  }, {
    columnHeaders: properties({ autoSizeColumnWidth: literal(true), columnAdjustment: literal('growToFit'), wordWrap: literal(true), fontSize: literal(9) }),
    values: properties({ wordWrap: literal(true), fontSize: literal(9) })
  });

  for (const visual of ucVisuals) write(target, `${ucPrefix}/visuals/${visual.name}/visual.json`, visual);
  write(target, `${ucPrefix}/page.json`, { $schema: pageSchema, name: ucPageId, displayName: ucPageName, displayOption: 'FitToPage', width: 1920, height: 1080, visualInteractions: [] });
  status.pages.splice(1, 0, { name: ucPageName, pageId: ucPageId, visuals: ucVisuals.length });
}

{
  const fabPageName = 'Fabric & FUAM System Tables';
  const fabPageId = identifier(fabPageName);
  const fabPrefix = `definition/pages/${fabPageId}`;
  const fabVisuals = [];
  let fabOrder = 0;
  function addFab(key, type, rectangle, title, queryState, objects = {}) {
    const name = identifier(`${fabPageName}:${key}`);
    const [x, y, width, height] = rectangle;
    assert.ok([x, y, width, height].every(value => value % 8 === 0), `Non-8px snapped bounds on ${key}: ${rectangle}`);
    assert.ok(x >= 0 && y >= 0 && x + width <= 1920 && y + height <= 1080, `Out-of-bounds: ${key}`);
    const visual = { $schema: visualSchema, name, position: { x, y, width, height, z: fabOrder, tabOrder: fabOrder }, visual: {
      visualType: type, objects,
      visualContainerObjects: {
        title: properties({ show: literal(Boolean(title)), text: literal(title || ''), fontSize: literal(13), titleWrap: literal(true) }),
        general: properties({ altText: literal(title || key) }),
        background: properties({ show: literal(true), color: solid('#FFFFFF'), transparency: literal(0) }),
        border: properties({ show: literal(true), color: solid('#E0E4E8'), radius: literal(4), width: literal(1) }),
        padding: properties({ top: literal(8), bottom: literal(8), left: literal(8), right: literal(8) }),
        visualHeader: properties({ show: literal(true) })
      }
    } };
    if (queryState) visual.visual.query = { queryState };
    fabOrder += 1000;
    fabVisuals.push(visual);
    return visual;
  }
  function textFab(key, rectangle, value, size = 14) {
    const visual = addFab(key, 'textbox', rectangle, '', null, { general: properties({ paragraphs: [{ textRuns: [{ value, textStyle: { fontFamily: 'Segoe UI', fontSize: `${size}pt`, color: '#242424' } }] }] }) });
    visual.visual.visualContainerObjects.background = properties({ show: literal(false) });
    visual.visual.visualContainerObjects.border = properties({ show: literal(false), radius: literal(0) });
    visual.visual.visualContainerObjects.padding = properties({ top: literal(0), bottom: literal(0), left: literal(0), right: literal(0) });
    return visual;
  }
  function heroFabCard(key, rectangle, topTitle, ref, subLabel, color = '#00786B') {
    const proj = binding(ref);
    return addFab(key, 'cardVisual', rectangle, topTitle, { Data: { projections: [proj] } }, {
      outline: instance({ show: literal(false) }),
      padding: instance({ paddingUniform: literal(4) }),
      layout: instance({ paddingUniform: literal(0) }),
      spacing: instance({ verticalSpacing: literal(2) }),
      value: instance({ fontSize: literal(34), fontColor: solid(color), labelDisplayUnits: literal('1') }),
      label: instance({ show: literal(true), text: literal(subLabel), fontSize: literal(11), fontColor: solid('#595959') })
    });
  }

  textFab('page_title', [32, 32, 1096, 40], 'Fabric Estate & Capacity Observability (powered by FUAM)', 22);
  textFab('evidence_strip', [32, 80, 1096, 40], 'FUAM_Lakehouse | 36 Workspaces, 532 Active Items, 4 Capacities | Aug 17-28 CU & operation telemetry', 11);
  addFab('navigation', 'pageNavigator', [32, 128, 1096, 48], '', null, {
    text: dual({ show: literal(true), fontSize: literal(11), fontFamily: literal('Segoe UI'), fontColor: solid('#242424') }),
    fill: dual({ show: literal(true), fillColor: solid('#EDF2F1'), transparency: literal(0) })
  });
  const fabAgentBtn = addFab('launch_data_agent', 'actionButton', [1152, 128, 736, 48], '', null, {
    text: dual({ show: literal(true), text: literal('Ask ZavaFinOps_Agent (Open Grounded Fabric Data Agent Q&A)'), fontSize: literal(11), bold: literal(true), fontColor: solid('#FFFFFF') }),
    fill: dual({ show: literal(true), fillColor: solid('#00786B'), transparency: literal(0) })
  });
  fabAgentBtn.visual.visualContainerObjects.visualLink = properties({
    show: literal(true),
    type: literal('WebUrl'),
    webUrl: literal(dataAgentWebUrl),
    tooltip: literal('Open ZavaFinOps_Agent in Fabric')
  });

  addFab('domain_slicer', 'slicer', [1152, 32, 360, 88], '', { Values: { projections: [binding('Fabric Estate Inventory[Workload Domain]')] } }, {
    data: properties({ mode: literal('Dropdown') }),
    header: properties({ show: literal(true), text: literal('Fabric Workload Domain') }),
    selection: properties({ strictSingleSelect: literal(false), selectAllCheckboxEnabled: literal(true) })
  });
  addFab('workspace_slicer', 'slicer', [1528, 32, 360, 88], '', { Values: { projections: [binding('Fabric Estate Inventory[Workspace Name]')] } }, {
    data: properties({ mode: literal('Dropdown') }),
    header: properties({ show: literal(true), text: literal('Fabric Workspace (36)') }),
    selection: properties({ strictSingleSelect: literal(false), selectAllCheckboxEnabled: literal(true) })
  });

  heroFabCard('fab_workspaces', [32, 192, 288, 160], 'Workspaces', 'Fabric Estate Inventory[Fabric Workspaces]', 'Workspaces  |  ⟳ FUAM_Lakehouse', '#242424');
  heroFabCard('fab_items', [336, 192, 288, 160], 'Active Items', 'Fabric Estate Inventory[Fabric Active Items]', 'Active Items  |  ⟳ FUAM_Lakehouse', '#00786B');
  heroFabCard('fab_capacities', [640, 192, 288, 160], 'Capacities', 'Fabric Estate Inventory[Fabric Capacities]', 'Capacities  |  ⟳ FUAM_Lakehouse', '#006DAA');

  const itemTypeCol = addFab('items_by_type', 'columnChart', [944, 192, 944, 344], 'Active Items by Artifact Type (Notebook, SemanticModel, SQLEndpoint, Lakehouse, Report...)', {
    Category: { projections: [binding('Fabric Estate Inventory[Item Type]')] },
    Y: { projections: [binding('Fabric Estate Inventory[Fabric Active Items]')] }
  }, {
    dataPoint: [{ properties: { defaultColor: solid('#00786B') } }],
    labels: properties({ show: literal(true) })
  });
  itemTypeCol.visual.query.sortDefinition = { sort: [{ field: binding('Fabric Estate Inventory[Fabric Active Items]').field, direction: 'Descending' }], isDefaultSort: true };

  addFab('ops_by_kind_area', 'areaChart', [32, 368, 592, 288], 'Daily Operations by Item Kind (Aug 17-28 FUAM)', {
    Category: { projections: [binding('Calendar[Date]')] },
    Series: { projections: [binding('Fabric Operations[Item Kind]')] },
    Y: { projections: [binding('Fabric Operations[Fabric Operation Count]')] }
  }, {
    categoryAxis: properties({ axisType: literal('Categorical'), showAxisTitle: literal(false), fontSize: literal(9) }),
    legend: properties({ show: literal(true), position: literal('Bottom'), fontSize: literal(9) })
  });

  addFab('cu_by_kind_donut', 'donutChart', [640, 368, 288, 288], 'Observed CU Share by Item Kind', {
    Category: { projections: [binding('Fabric Operations[Item Kind]')] },
    Y: { projections: [binding('Fabric Operations[Observed CUs]')] }
  }, {
    labels: properties({ show: literal(true), labelStyle: literal('Percent of total'), fontSize: literal(10) }),
    legend: properties({ show: literal(true), position: literal('Bottom'), fontSize: literal(9) })
  });

  addFab('cu_burn_area', 'areaChart', [32, 672, 904, 376], 'Daily Observed CU Consumption by Item Kind (FUAM TotalCUs — 1,021,021.69 CUs)', {
    Category: { projections: [binding('Calendar[Date]')] },
    Series: { projections: [binding('Fabric Operations[Item Kind]')] },
    Y: { projections: [binding('Fabric Operations[Observed CUs]')] }
  }, {
    categoryAxis: properties({ axisType: literal('Categorical'), showAxisTitle: literal(false), fontSize: literal(9) }),
    legend: properties({ show: literal(true), position: literal('Bottom'), fontSize: literal(9) })
  });

  addFab('items_by_domain_donut', 'donutChart', [952, 552, 456, 496], 'Fabric Items by Workload Domain (532 items)', {
    Category: { projections: [binding('Fabric Estate Inventory[Workload Domain]')] },
    Y: { projections: [binding('Fabric Estate Inventory[Fabric Active Items]')] }
  }, {
    labels: properties({ show: literal(true), labelStyle: literal('Data value, percent of total'), fontSize: literal(10) }),
    legend: properties({ show: literal(true), position: literal('Right'), fontSize: literal(10) })
  });

  addFab('fabric_estate_ledger', 'tableEx', [1424, 552, 464, 496], 'FUAM Active Item Ledger (532 items across 36 workspaces)', {
    Values: { projections: ['Fabric Estate Inventory[Workspace Name]', 'Fabric Estate Inventory[Item Name]', 'Fabric Estate Inventory[Item Type]', 'Fabric Estate Inventory[Workload Domain]', 'Fabric Estate Inventory[Capacity SKU]'].map(binding) }
  }, {
    columnHeaders: properties({ autoSizeColumnWidth: literal(true), columnAdjustment: literal('growToFit'), wordWrap: literal(true), fontSize: literal(9) }),
    values: properties({ wordWrap: literal(true), fontSize: literal(9) })
  });

  for (const visual of fabVisuals) write(target, `${fabPrefix}/visuals/${visual.name}/visual.json`, visual);
  write(target, `${fabPrefix}/page.json`, { $schema: pageSchema, name: fabPageId, displayName: fabPageName, displayOption: 'FitToPage', width: 1920, height: 1080, visualInteractions: [] });
  status.pages.splice(2, 0, { name: fabPageName, pageId: fabPageId, visuals: fabVisuals.length });
}

{
  const fdryPageName = 'Microsoft Foundry & AI Usage';
  const fdryPageId = identifier(fdryPageName);
  const fdryPrefix = `definition/pages/${fdryPageId}`;
  const fdryVisuals = [];
  let fdryOrder = 0;
  function addFdry(key, type, rectangle, title, queryState, objects = {}) {
    const name = identifier(`${fdryPageName}:${key}`);
    const [x, y, width, height] = rectangle;
    assert.ok([x, y, width, height].every(value => value % 8 === 0), `Non-8px snapped bounds on ${key}: ${rectangle}`);
    assert.ok(x >= 0 && y >= 0 && x + width <= 1920 && y + height <= 1080, `Out-of-bounds: ${key}`);
    const visual = { $schema: visualSchema, name, position: { x, y, width, height, z: fdryOrder, tabOrder: fdryOrder }, visual: {
      visualType: type, objects,
      visualContainerObjects: {
        title: properties({ show: literal(Boolean(title)), text: literal(title || ''), fontSize: literal(13), titleWrap: literal(true) }),
        general: properties({ altText: literal(title || key) }),
        background: properties({ show: literal(true), color: solid('#FFFFFF'), transparency: literal(0) }),
        border: properties({ show: literal(true), color: solid('#E0E4E8'), radius: literal(4), width: literal(1) }),
        padding: properties({ top: literal(8), bottom: literal(8), left: literal(8), right: literal(8) }),
        visualHeader: properties({ show: literal(true) })
      }
    } };
    if (queryState) visual.visual.query = { queryState };
    fdryOrder += 1000;
    fdryVisuals.push(visual);
    return visual;
  }
  function textFdry(key, rectangle, value, size = 14) {
    const visual = addFdry(key, 'textbox', rectangle, '', null, { general: properties({ paragraphs: [{ textRuns: [{ value, textStyle: { fontFamily: 'Segoe UI', fontSize: `${size}pt`, color: '#242424' } }] }] }) });
    visual.visual.visualContainerObjects.background = properties({ show: literal(false) });
    visual.visual.visualContainerObjects.border = properties({ show: literal(false), radius: literal(0) });
    visual.visual.visualContainerObjects.padding = properties({ top: literal(0), bottom: literal(0), left: literal(0), right: literal(0) });
    return visual;
  }
  function heroFdryCard(key, rectangle, topTitle, ref, subLabel, color = '#006DAA') {
    const proj = binding(ref);
    return addFdry(key, 'cardVisual', rectangle, topTitle, { Data: { projections: [proj] } }, {
      outline: instance({ show: literal(false) }),
      padding: instance({ paddingUniform: literal(4) }),
      layout: instance({ paddingUniform: literal(0) }),
      spacing: instance({ verticalSpacing: literal(2) }),
      value: instance({ fontSize: literal(34), fontColor: solid(color), labelDisplayUnits: literal('1') }),
      label: instance({ show: literal(true), text: literal(subLabel), fontSize: literal(11), fontColor: solid('#595959') })
    });
  }

  textFdry('page_title', [32, 32, 1096, 40], 'Microsoft Foundry & AI Observability (Foundry Models & Tools)', 22);
  textFdry('evidence_strip', [32, 80, 1096, 40], 'Foundry Sandbox (zava-foundry-sandbox) | 18 Foundry Accounts, 45 AI Model & Tool Meters, 13 RGs | Jul–Sep 2026 ($52.11 USD)', 11);
  addFdry('navigation', 'pageNavigator', [32, 128, 1096, 48], '', null, {
    text: dual({ show: literal(true), fontSize: literal(10), fontFamily: literal('Segoe UI'), fontColor: solid('#242424') }),
    fill: dual({ show: literal(true), fillColor: solid('#EDF2F1'), transparency: literal(0) })
  });
  const fdryAgentBtn = addFdry('launch_data_agent', 'actionButton', [1152, 128, 736, 48], '', null, {
    text: dual({ show: literal(true), text: literal('Ask ZavaFinOps_Agent (Open Grounded Fabric Data Agent Q&A)'), fontSize: literal(11), bold: literal(true), fontColor: solid('#FFFFFF') }),
    fill: dual({ show: literal(true), fillColor: solid('#006DAA'), transparency: literal(0) })
  });
  fdryAgentBtn.visual.visualContainerObjects.visualLink = properties({
    show: literal(true),
    type: literal('WebUrl'),
    webUrl: literal(dataAgentWebUrl),
    tooltip: literal('Open ZavaFinOps_Agent in Fabric')
  });

  addFdry('family_slicer', 'slicer', [1152, 32, 360, 88], '', { Values: { projections: [binding('Foundry AI Usage[Model Family]')] } }, {
    data: properties({ mode: literal('Dropdown') }),
    header: properties({ show: literal(true), text: literal('Foundry Model & Compute Family') }),
    selection: properties({ strictSingleSelect: literal(false), selectAllCheckboxEnabled: literal(true) })
  });
  addFdry('account_slicer', 'slicer', [1528, 32, 360, 88], '', { Values: { projections: [binding('Foundry AI Usage[Foundry Account]')] } }, {
    data: properties({ mode: literal('Dropdown') }),
    header: properties({ show: literal(true), text: literal('Foundry Account / Project (18)') }),
    selection: properties({ strictSingleSelect: literal(false), selectAllCheckboxEnabled: literal(true) })
  });

  heroFdryCard('fdry_accounts', [32, 192, 288, 160], 'Foundry Accounts', 'Foundry AI Usage[Foundry Accounts]', 'Accounts & Projects  |  ⟳ Foundry', '#242424');
  heroFdryCard('fdry_meters', [336, 192, 288, 160], 'AI Meters', 'Foundry AI Usage[Foundry AI Meters]', 'Active Model & Tool Meters', '#006DAA');
  heroFdryCard('fdry_cost', [640, 192, 288, 160], 'Foundry Spend', 'Foundry AI Usage[Foundry Cost USD]', '224 Daily Meter Rows  |  ⟳ Jul–Sep', '#00786B');

  const fdryFamilyCol = addFdry('spend_by_model_family', 'columnChart', [944, 192, 944, 344], 'Foundry Spend by AI Model & Compute Family (GPT-5.4, Sora 2 & Image 2, Hosted Agent vCPU/RAM, Evaluations...)', {
    Category: { projections: [binding('Foundry AI Usage[Model Family]')] },
    Y: { projections: [binding('Foundry AI Usage[Foundry Cost USD]')] }
  }, {
    dataPoint: [{ properties: { defaultColor: solid('#006DAA') } }],
    labels: properties({ show: literal(true) })
  });
  fdryFamilyCol.visual.query.sortDefinition = { sort: [{ field: binding('Foundry AI Usage[Foundry Cost USD]').field, direction: 'Descending' }], isDefaultSort: true };

  addFdry('activity_by_service_area', 'areaChart', [32, 368, 592, 288], 'Daily AI Meter Activity by Service (Foundry Models vs. Foundry Tools)', {
    Category: { projections: [binding('Foundry AI Usage[Date Label]')] },
    Series: { projections: [binding('Foundry AI Usage[Service Name]')] },
    Y: { projections: [binding('Foundry AI Usage[Foundry Daily Meter Rows]')] }
  }, {
    categoryAxis: properties({ axisType: literal('Categorical'), showAxisTitle: literal(false), fontSize: literal(9) }),
    legend: properties({ show: literal(true), position: literal('Bottom'), fontSize: literal(9) })
  });

  addFdry('spend_by_service_donut', 'donutChart', [640, 368, 288, 288], 'Spend Share by Service (Models vs. Tools)', {
    Category: { projections: [binding('Foundry AI Usage[Service Name]')] },
    Y: { projections: [binding('Foundry AI Usage[Foundry Cost USD]')] }
  }, {
    labels: properties({ show: literal(true), labelStyle: literal('Percent of total'), fontSize: literal(10) }),
    legend: properties({ show: literal(true), position: literal('Bottom'), fontSize: literal(9) })
  });

  addFdry('daily_spend_by_family_area', 'areaChart', [32, 672, 904, 376], 'Daily Foundry Spend USD by Model & Compute Family (Jul 6 – Sep 30, 2026 — $52.11 Total)', {
    Category: { projections: [binding('Foundry AI Usage[Date Label]')] },
    Series: { projections: [binding('Foundry AI Usage[Model Family]')] },
    Y: { projections: [binding('Foundry AI Usage[Foundry Cost USD]')] }
  }, {
    categoryAxis: properties({ axisType: literal('Categorical'), showAxisTitle: literal(false), fontSize: literal(9) }),
    legend: properties({ show: literal(true), position: literal('Bottom'), fontSize: literal(9) })
  });

  addFdry('spend_by_modality_donut', 'donutChart', [952, 552, 456, 496], 'Spend by Token & Compute Modality (Multimodal, Output/Input Tokens, Hosted vCPU/RAM, Evaluations)', {
    Category: { projections: [binding('Foundry AI Usage[Usage Modality]')] },
    Y: { projections: [binding('Foundry AI Usage[Foundry Cost USD]')] }
  }, {
    labels: properties({ show: literal(true), labelStyle: literal('Data value, percent of total'), fontSize: literal(9) }),
    legend: properties({ show: literal(true), position: literal('Right'), fontSize: literal(9) })
  });

  addFdry('foundry_meter_ledger', 'tableEx', [1424, 552, 464, 496], 'Microsoft Foundry Daily Meter Ledger (224 rows across 18 accounts)', {
    Values: { projections: ['Foundry AI Usage[Date Label]', 'Foundry AI Usage[Foundry Account]', 'Foundry AI Usage[Service Name]', 'Foundry AI Usage[Model Family]', 'Foundry AI Usage[Meter]', 'Foundry AI Usage[Foundry Cost USD]'].map(binding) }
  }, {
    columnHeaders: properties({ autoSizeColumnWidth: literal(true), columnAdjustment: literal('growToFit'), wordWrap: literal(true), fontSize: literal(9) }),
    values: properties({ wordWrap: literal(true), fontSize: literal(9) })
  });

  for (const visual of fdryVisuals) write(target, `${fdryPrefix}/visuals/${visual.name}/visual.json`, visual);
  write(target, `${fdryPrefix}/page.json`, { $schema: pageSchema, name: fdryPageId, displayName: fdryPageName, displayOption: 'FitToPage', width: 1920, height: 1080, visualInteractions: [] });
  status.pages.splice(3, 0, { name: fdryPageName, pageId: fdryPageId, visuals: fdryVisuals.length });
}

{
  const anomPageName = 'Anomalies & Advisor Insights';
  const anomPageId = identifier(anomPageName);
  const anomPrefix = `definition/pages/${anomPageId}`;
  const anomVisuals = [];
  let anomOrder = 0;
  function addAnom(key, type, rectangle, title, queryState, objects = {}) {
    const name = identifier(`${anomPageName}:${key}`);
    const [x, y, width, height] = rectangle;
    assert.ok([x, y, width, height].every(value => value % 8 === 0), `Non-8px snapped bounds on ${key}: ${rectangle}`);
    assert.ok(x >= 0 && y >= 0 && x + width <= 1920 && y + height <= 1080, `Out-of-bounds: ${key}`);
    const visual = { $schema: visualSchema, name, position: { x, y, width, height, z: anomOrder, tabOrder: anomOrder }, visual: {
      visualType: type, objects,
      visualContainerObjects: {
        title: properties({ show: literal(Boolean(title)), text: literal(title || ''), fontSize: literal(13), titleWrap: literal(true) }),
        general: properties({ altText: literal(title || key) }),
        background: properties({ show: literal(true), color: solid('#FFFFFF'), transparency: literal(0) }),
        border: properties({ show: literal(true), color: solid('#E0E4E8'), radius: literal(4), width: literal(1) }),
        padding: properties({ top: literal(8), bottom: literal(8), left: literal(8), right: literal(8) }),
        visualHeader: properties({ show: literal(true) })
      }
    } };
    if (queryState) visual.visual.query = { queryState };
    anomOrder += 1000;
    anomVisuals.push(visual);
    return visual;
  }
  function textAnom(key, rectangle, value, size = 14) {
    const visual = addAnom(key, 'textbox', rectangle, '', null, { general: properties({ paragraphs: [{ textRuns: [{ value, textStyle: { fontFamily: 'Segoe UI', fontSize: `${size}pt`, color: '#242424' } }] }] }) });
    visual.visual.visualContainerObjects.background = properties({ show: literal(false) });
    visual.visual.visualContainerObjects.border = properties({ show: literal(false), radius: literal(0) });
    visual.visual.visualContainerObjects.padding = properties({ top: literal(0), bottom: literal(0), left: literal(0), right: literal(0) });
    return visual;
  }
  function heroAnomCard(key, rectangle, topTitle, ref, subLabel, color = '#B94A32') {
    const proj = binding(ref);
    return addAnom(key, 'cardVisual', rectangle, topTitle, { Data: { projections: [proj] } }, {
      outline: instance({ show: literal(false) }),
      padding: instance({ paddingUniform: literal(4) }),
      layout: instance({ paddingUniform: literal(0) }),
      spacing: instance({ verticalSpacing: literal(2) }),
      value: instance({ fontSize: literal(32), fontColor: solid(color), labelDisplayUnits: literal('1') }),
      label: instance({ show: literal(true), text: literal(subLabel), fontSize: literal(10), fontColor: solid('#595959') })
    });
  }

  textAnom('page_title', [32, 32, 1096, 40], 'Anomaly Detection, Cost Alerts & Azure Advisor Recommendations', 22);
  textAnom('evidence_strip', [32, 80, 1096, 40], 'Scheduled Action: dailyanomalybyresourcegroup (ms:DailyAnomalyByResourceGroup, 8/19–10/19) | Oct 17 +4.28% Anomaly (rg-fabric +17.99% / 87.65%) + 49 Live Advisor Findings', 10);
  addAnom('navigation', 'pageNavigator', [32, 128, 1096, 48], '', null, {
    text: dual({ show: literal(true), fontSize: literal(10), fontFamily: literal('Segoe UI'), fontColor: solid('#242424') }),
    fill: dual({ show: literal(true), fillColor: solid('#EDF2F1'), transparency: literal(0) })
  });
  const anomAgentBtn = addAnom('launch_data_agent', 'actionButton', [1152, 128, 736, 48], '', null, {
    text: dual({ show: literal(true), text: literal('Ask ZavaFinOps_Agent (Investigate Anomalies & Recommendations)'), fontSize: literal(11), bold: literal(true), fontColor: solid('#FFFFFF') }),
    fill: dual({ show: literal(true), fillColor: solid('#B94A32'), transparency: literal(0) })
  });
  anomAgentBtn.visual.visualContainerObjects.visualLink = properties({
    show: literal(true),
    type: literal('WebUrl'),
    webUrl: literal(dataAgentWebUrl),
    tooltip: literal('Open ZavaFinOps_Agent in Fabric')
  });

  addAnom('severity_slicer', 'slicer', [1152, 32, 360, 88], '', { Values: { projections: [binding('Anomalies & Insights[Severity]')] } }, {
    data: properties({ mode: literal('Dropdown') }),
    header: properties({ show: literal(true), text: literal('Alert & Advisor Severity') }),
    selection: properties({ strictSingleSelect: literal(false), selectAllCheckboxEnabled: literal(true) })
  });
  addAnom('rg_slicer', 'slicer', [1528, 32, 360, 88], '', { Values: { projections: [binding('Daily RG Anomalies[Resource Group]')] } }, {
    data: properties({ mode: literal('Dropdown') }),
    header: properties({ show: literal(true), text: literal('Resource Group (Daily Anomaly View)') }),
    selection: properties({ strictSingleSelect: literal(false), selectAllCheckboxEnabled: literal(true) })
  });

  heroAnomCard('detected_anomalies', [32, 192, 288, 160], 'Daily RG Spikes', 'Daily RG Anomalies[Detected Anomalies]', 'Oct 17 Email (+4.28% vs expected)', '#B32324');
  heroAnomCard('high_severity', [336, 192, 288, 160], 'High Severity', 'Anomalies & Insights[High Severity Insights]', 'High-Impact Alerts & Advisor', '#B94A32');
  heroAnomCard('advisor_count', [640, 192, 288, 160], 'Advisor Findings', 'Anomalies & Insights[Advisor Recommendations]', 'Live Microsoft.Advisor (49)', '#006DAA');

  const rgCol = addAnom('rg_cost_by_group', 'columnChart', [944, 192, 944, 344], 'Resource Group Spend in Anomaly Evaluation Window (Aug 19 – Oct 19: rg-fabric 87.65% of Oct 17 spike)', {
    Category: { projections: [binding('Daily RG Anomalies[Resource Group]')] },
    Y: { projections: [binding('Daily RG Anomalies[Anomaly Window Cost USD]')] }
  }, {
    dataPoint: [{ properties: { defaultColor: solid('#B94A32') } }],
    labels: properties({ show: literal(true) })
  });
  rgCol.visual.query.sortDefinition = { sort: [{ field: binding('Daily RG Anomalies[Anomaly Window Cost USD]').field, direction: 'Descending' }], isDefaultSort: true };

  addAnom('daily_rg_trend_area', 'areaChart', [32, 368, 592, 288], 'Daily Cost Trend vs. Expected 7-Day Baseline (ms:DailyAnomalyByResourceGroup)', {
    Category: { projections: [binding('Daily RG Anomalies[Date Label]')] },
    Y: { projections: [binding('Daily RG Anomalies[Anomaly Window Cost USD]'), binding('Daily RG Anomalies[Expected Baseline USD]')] }
  }, {
    dataPoint: [
      { selector: { metadata: 'Daily RG Anomalies.Anomaly Window Cost USD' }, properties: { fill: solid('#B94A32') } },
      { selector: { metadata: 'Daily RG Anomalies.Expected Baseline USD' }, properties: { fill: solid('#006DAA') } }
    ],
    categoryAxis: properties({ axisType: literal('Categorical'), showAxisTitle: literal(false), fontSize: literal(9) }),
    legend: properties({ show: literal(true), position: literal('Bottom'), fontSize: literal(9) })
  });

  addAnom('insights_by_severity_donut', 'donutChart', [640, 368, 288, 288], 'Findings by Severity (High / Medium / Low)', {
    Category: { projections: [binding('Anomalies & Insights[Severity]')] },
    Y: { projections: [binding('Anomalies & Insights[Active Insights & Alerts]')] }
  }, {
    labels: properties({ show: literal(true), labelStyle: literal('Data value, percent of total'), fontSize: literal(9) }),
    legend: properties({ show: literal(true), position: literal('Bottom'), fontSize: literal(9) })
  });

  addAnom('daily_rg_stacked_area', 'areaChart', [32, 672, 904, 376], 'Daily Spend by Resource Group (Email Alert Window 8/19–10/19: rg-fabric +17.99%, rg-admin-1750 -13.69%, purview-rg -12.93%)', {
    Category: { projections: [binding('Daily RG Anomalies[Date Label]')] },
    Series: { projections: [binding('Daily RG Anomalies[Resource Group]')] },
    Y: { projections: [binding('Daily RG Anomalies[Anomaly Window Cost USD]')] }
  }, {
    categoryAxis: properties({ axisType: literal('Categorical'), showAxisTitle: literal(false), fontSize: literal(9) }),
    legend: properties({ show: literal(true), position: literal('Bottom'), fontSize: literal(9) })
  });

  addAnom('insights_ledger', 'tableEx', [952, 552, 936, 496], 'Unified Anomaly Alert, Budget Threshold & Azure Advisor Action Ledger (56 live items)', {
    Values: { projections: ['Anomalies & Insights[Detected Date]', 'Anomalies & Insights[Category]', 'Anomalies & Insights[Severity]', 'Anomalies & Insights[Resource Group]', 'Anomalies & Insights[Metric Or Change]', 'Anomalies & Insights[Title]', 'Anomalies & Insights[Recommendation]'].map(binding) }
  }, {
    columnHeaders: properties({ autoSizeColumnWidth: literal(true), columnAdjustment: literal('growToFit'), wordWrap: literal(true), fontSize: literal(9) }),
    values: properties({ wordWrap: literal(true), fontSize: literal(9) })
  });

  for (const visual of anomVisuals) write(target, `${anomPrefix}/visuals/${visual.name}/visual.json`, visual);
  write(target, `${anomPrefix}/page.json`, { $schema: pageSchema, name: anomPageId, displayName: anomPageName, displayOption: 'FitToPage', width: 1920, height: 1080, visualInteractions: [] });
  status.pages.splice(5, 0, { name: anomPageName, pageId: anomPageId, visuals: anomVisuals.length });
}

write(target, 'definition/pages/pages.json', { ...sourcePages, pageOrder: status.pages.map(page => page.pageId), activePageName: status.pages[0].pageId });
write(join(root, 'artifacts'), 'operating-demo-build.json', status);
console.log(JSON.stringify(status, null, 2));
}