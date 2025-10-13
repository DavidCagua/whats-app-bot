import NextAuth from "next-auth"
import Credentials from "next-auth/providers/credentials"
import { compare } from "bcryptjs"
import { prisma } from "./prisma"

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

        // Find user in database
        const user = await prisma.users.findUnique({
          where: {
            email: credentials.email as string,
          },
        })

        if (!user) {
          throw new Error("Invalid email or password")
        }

        // Check if user is active
        if (!user.is_active) {
          throw new Error("Account is disabled")
        }

        // Check if user is super admin
        if (user.role !== "super_admin") {
          throw new Error("Access denied. Super admin privileges required.")
        }
        console.log("user", user)
        // Verify password
        const isPasswordValid = await compare(
          credentials.password as string,
          user.password_hash
        )

        if (!isPasswordValid) {
          throw new Error("Invalid email or password")
        }

        // Return user object
        return {
          id: user.id,
          email: user.email || "",
          name: user.full_name || "",
          role: user.role || "",
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
      }
      return token
    },
    async session({ session, token }) {
      if (token && session.user) {
        session.user.id = token.id as string
        session.user.role = token.role as string
      }
      return session
    },
  },
})
