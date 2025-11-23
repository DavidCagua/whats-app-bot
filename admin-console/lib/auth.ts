import NextAuth from "next-auth"
import Credentials from "next-auth/providers/credentials"
import { compare } from "bcryptjs"
import { prisma } from "./prisma"

export type UserBusiness = {
  businessId: string
  businessName: string
  role: string
}

export const { handlers, signIn, signOut, auth } = NextAuth({
  providers: [
    Credentials({
      credentials: {
        email: { label: "Email", type: "email" },
        password: { label: "Password", type: "password" },
      },
      authorize: async (credentials) => {
        if (!credentials?.email || !credentials?.password) {
          throw new Error("Missing email or password")
        }

        // Find user in database with their business associations
        const user = await prisma.users.findUnique({
          where: {
            email: credentials.email as string,
          },
          include: {
            user_businesses: {
              include: {
                businesses: true,
              },
            },
          },
        })

        if (!user) {
          throw new Error("Invalid email or password")
        }

        // Check if user is active
        if (!user.is_active) {
          throw new Error("Account is disabled")
        }

        // User must be super_admin OR have at least one business association
        const isSuperAdmin = user.role === "super_admin"
        const hasBusinessAccess = user.user_businesses.length > 0

        if (!isSuperAdmin && !hasBusinessAccess) {
          throw new Error("Access denied. No business access configured.")
        }

        // Verify password
        const isPasswordValid = await compare(
          credentials.password as string,
          user.password_hash
        )

        if (!isPasswordValid) {
          throw new Error("Invalid email or password")
        }

        // Build business associations array
        const businesses: UserBusiness[] = user.user_businesses.map((ub) => ({
          businessId: ub.business_id,
          businessName: ub.businesses.name,
          role: ub.role || "staff",
        }))

        // Return user object with businesses
        return {
          id: user.id,
          email: user.email || "",
          name: user.full_name || "",
          role: user.role || "",
          businesses: JSON.stringify(businesses),
        }
      },
    }),
  ],
  pages: {
    signIn: "/login",
  },
  callbacks: {
    async jwt({ token, user }) {
      if (user) {
        token.id = user.id
        token.role = user.role
        token.businesses = user.businesses
      }
      return token
    },
    async session({ session, token }) {
      if (token && session.user) {
        session.user.id = token.id as string
        session.user.role = token.role as string
        session.user.businesses = token.businesses
          ? JSON.parse(token.businesses as string)
          : []
      }
      return session
    },
  },
})
