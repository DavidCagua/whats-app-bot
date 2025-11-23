import { DefaultSession } from "next-auth"

export type UserBusiness = {
  businessId: string
  businessName: string
  role: string // 'admin' or 'staff'
}

declare module "next-auth" {
  interface Session {
    user: {
      id: string
      role: string // 'super_admin' or empty for business users
      businesses: UserBusiness[]
    } & DefaultSession["user"]
  }

  interface User {
    id: string
    role: string
    businesses: string // JSON stringified for JWT transport
  }
}

declare module "next-auth/jwt" {
  interface JWT {
    id: string
    role: string
    businesses: string // JSON stringified
  }
}
