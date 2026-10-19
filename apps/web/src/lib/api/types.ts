/**
 * Thin re-export layer over the generated schema so the rest of the app never
 * imports `schema.d.ts` directly.
 */
import type { components } from './schema';

type Schemas = components['schemas'];

export type LoginRequest = Schemas['LoginRequest'];
export type LogoutRequest = Schemas['LogoutRequest'];
export type Message = Schemas['Message'];
export type RefreshRequest = Schemas['RefreshRequest'];
export type RoleRead = Schemas['RoleRead'];
export type Token = Schemas['Token'];
export type UserCreate = Schemas['UserCreate'];
export type UserList = Schemas['UserList'];
export type UserRead = Schemas['UserRead'];
export type UserUpdate = Schemas['UserUpdate'];

// F2 nodes
export type NodeStatus = Schemas['NodeStatus'];
export type NodeRead = Schemas['NodeRead'];
export type NodeList = Schemas['NodeList'];

// F3 quotas
export type QuotaRead = Schemas['QuotaRead'];
export type QuotaList = Schemas['QuotaList'];

// F4 jobs
export type JobStatus = Schemas['JobStatus'];
export type JobCreate = Schemas['JobCreate'];
export type JobRead = Schemas['JobRead'];
export type JobDetail = Schemas['JobDetail'];
export type JobList = Schemas['JobList'];

// F5 usage
export type UsageEventRead = Schemas['UsageEventRead'];
export type UserUsage = Schemas['UserUsage'];
export type UserUsageRank = Schemas['UserUsageRank'];
export type GroupUsageRanking = Schemas['GroupUsageRanking'];
