// Types generated from the API's OpenAPI document (`make types`). CI regenerates them and fails if they
// differ, so the web app can never drift from the API.
import type { components, paths } from "./schema";

export type { components, paths };
type Schemas = components["schemas"];

export type Health = Schemas["Health"];
export type Readiness = Schemas["Readiness"];
export type ApiError = Schemas["ErrorResponse"];
export type Me = Schemas["Me"];
export type Plan = Schemas["PlanOut"];
export type Strategy = Schemas["StrategyOut"];
export type StrategyInput = Schemas["StrategyIn"];
export type StrategyPatch = Schemas["StrategyPatch"];
export type StrategyPage = Schemas["Page_StrategyOut_"];
export type StrategyConfig = Strategy["config"];
export type TimeBasedConfig = Schemas["TimeBasedConfig"];
export type RangeBreakoutConfig = Schemas["RangeBreakoutConfig"];
export type ZeroDteConfig = Schemas["ZeroDteConfig"];
export type StrategyLeg = Schemas["Leg"];
export type LegStrike = Schemas["Strike"];
export type LegThreshold = Schemas["Threshold"];
export type LegTrailing = Schemas["Trailing"];
export type LegReEntry = Schemas["ReEntry"];
export type StrategyRisk = Schemas["StrategyRisk"];
export type StrategyCatalog = Schemas["StrategyCatalogOut"];
export type Instrument = Schemas["InstrumentOut"];
export type StrategyPreset = Schemas["PresetOut"];
export type ConfigIssue = Schemas["ConfigIssue"];
export type ConfigValidation = Schemas["ValidateOut"];
export type Entitlements = Schemas["EntitlementsOut"];
export type UsageItem = Schemas["UsageItem"];
export type FeatureInfo = Schemas["FeatureInfo"];
export type BrokerInfo = Schemas["BrokerInfoOut"];
export type BrokerAccount = Schemas["BrokerAccountOut"];
export type AdminUserRow = Schemas["AdminUserRow"];
export type AdminUserDetail = Schemas["AdminUserDetail"];
export type AdminUserPage = Schemas["Page_AdminUserRow_"];
export type AdminSubscription = Schemas["AdminSubscription"];
export type AuditEntry = Schemas["AuditEntry"];
export type AuditPage = Schemas["Page_AuditEntry_"];
export type InstrumentAdmin = Schemas["InstrumentAdminOut"];
export type InstrumentRefresh = Schemas["InstrumentRefreshOut"];
export type Overview = Schemas["Overview"];
export type PlanAdmin = Schemas["PlanAdminOut"];
export type MarketSnapshot = Schemas["MarketSnapshot"];
export type MarketQuote = Schemas["Quote"];
export type MarketDataAdmin = Schemas["MarketDataAdmin"];
