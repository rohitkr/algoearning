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
export type Entitlements = Schemas["EntitlementsOut"];
export type UsageItem = Schemas["UsageItem"];
export type FeatureInfo = Schemas["FeatureInfo"];
export type BrokerInfo = Schemas["BrokerInfoOut"];
export type BrokerAccount = Schemas["BrokerAccountOut"];
