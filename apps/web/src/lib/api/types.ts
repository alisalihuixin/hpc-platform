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
