import Link from "next/link"

export default function PrivacyPolicyPage() {
  return (
    <div className="min-h-screen bg-gray-50 py-12 px-4 sm:px-6 lg:px-8">
      <div className="max-w-4xl mx-auto bg-white shadow-sm rounded-lg p-8 md:p-12">
        <div className="mb-8">
          <Link
            href="/"
            className="text-sm text-blue-600 hover:text-blue-800 mb-4 inline-block"
          >
            ← Back to Home
          </Link>
          <h1 className="text-4xl font-bold text-gray-900 mb-4">Privacy Policy</h1>
          <p className="text-sm text-gray-600">Last updated: {new Date().toLocaleDateString()}</p>
        </div>

        <div className="prose prose-gray max-w-none space-y-6">
          <section>
            <h2 className="text-2xl font-semibold text-gray-900 mt-8 mb-4">1. Introduction</h2>
            <p className="text-gray-700 leading-relaxed">
              Welcome to our WhatsApp Bot service (&quot;Service&quot;, &quot;we&quot;, &quot;us&quot;, or &quot;our&quot;). This Privacy Policy explains how we collect, use, disclose, and safeguard your information when you use our multi-tenant WhatsApp appointment scheduling service.
            </p>
          </section>

          <section>
            <h2 className="text-2xl font-semibold text-gray-900 mt-8 mb-4">2. Information We Collect</h2>

            <h3 className="text-xl font-semibold text-gray-800 mt-6 mb-3">2.1 Information Collected Automatically</h3>
            <p className="text-gray-700 leading-relaxed">When you interact with our WhatsApp bot, we collect:</p>
            <ul className="list-disc pl-6 mt-2 space-y-2 text-gray-700">
              <li>WhatsApp phone number</li>
              <li>Message content and conversation history</li>
              <li>Appointment details (date, time, service type)</li>
              <li>Interaction timestamps</li>
            </ul>

            <h3 className="text-xl font-semibold text-gray-800 mt-6 mb-3">2.2 Business Account Information</h3>
            <p className="text-gray-700 leading-relaxed">For business administrators who use our admin console:</p>
            <ul className="list-disc pl-6 mt-2 space-y-2 text-gray-700">
              <li>Email address and account credentials</li>
              <li>Business information (name, services, pricing)</li>
              <li>Google Calendar credentials (encrypted)</li>
              <li>Business configuration and settings</li>
            </ul>
          </section>

          <section>
            <h2 className="text-2xl font-semibold text-gray-900 mt-8 mb-4">3. How We Use Your Information</h2>
            <p className="text-gray-700 leading-relaxed">We use the collected information for:</p>
            <ul className="list-disc pl-6 mt-2 space-y-2 text-gray-700">
              <li><strong>Appointment Scheduling:</strong> Processing and managing appointment requests through natural language conversations</li>
              <li><strong>Calendar Integration:</strong> Creating, updating, and managing appointments in Google Calendar</li>
              <li><strong>AI Processing:</strong> Using OpenAI services to understand and respond to customer messages</li>
              <li><strong>Service Improvement:</strong> Analyzing conversation patterns to improve bot responses and user experience</li>
              <li><strong>Communication:</strong> Sending appointment confirmations, reminders, and updates via WhatsApp</li>
            </ul>
          </section>

          <section>
            <h2 className="text-2xl font-semibold text-gray-900 mt-8 mb-4">4. Third-Party Services</h2>
            <p className="text-gray-700 leading-relaxed">Our Service integrates with the following third-party services:</p>

            <h3 className="text-xl font-semibold text-gray-800 mt-6 mb-3">4.1 Meta (WhatsApp Business API)</h3>
            <p className="text-gray-700 leading-relaxed">
              We use Meta&apos;s WhatsApp Business API to send and receive messages. Meta&apos;s privacy policy applies to their services: <a href="https://www.whatsapp.com/legal/privacy-policy" className="text-blue-600 hover:underline">https://www.whatsapp.com/legal/privacy-policy</a>
            </p>

            <h3 className="text-xl font-semibold text-gray-800 mt-6 mb-3">4.2 Google Calendar</h3>
            <p className="text-gray-700 leading-relaxed">
              We access Google Calendar to manage appointments. Each business authorizes access to their own calendar. Google&apos;s privacy policy: <a href="https://policies.google.com/privacy" className="text-blue-600 hover:underline">https://policies.google.com/privacy</a>
            </p>

            <h3 className="text-xl font-semibold text-gray-800 mt-6 mb-3">4.3 OpenAI</h3>
            <p className="text-gray-700 leading-relaxed">
              We use OpenAI&apos;s GPT models to process and respond to messages. OpenAI&apos;s privacy policy: <a href="https://openai.com/policies/privacy-policy" className="text-blue-600 hover:underline">https://openai.com/policies/privacy-policy</a>
            </p>
          </section>

          <section>
            <h2 className="text-2xl font-semibold text-gray-900 mt-8 mb-4">5. Data Security</h2>
            <p className="text-gray-700 leading-relaxed">We implement security measures to protect your information:</p>
            <ul className="list-disc pl-6 mt-2 space-y-2 text-gray-700">
              <li><strong>Encryption:</strong> Google Calendar credentials are encrypted using AES-256-GCM before storage</li>
              <li><strong>Secure Authentication:</strong> Admin console access is protected with secure authentication</li>
              <li><strong>HTTPS:</strong> All data transmission uses encrypted connections</li>
              <li><strong>Access Control:</strong> Multi-tenant architecture ensures each business can only access their own data</li>
            </ul>
          </section>

          <section>
            <h2 className="text-2xl font-semibold text-gray-900 mt-8 mb-4">6. Multi-Tenant Architecture</h2>
            <p className="text-gray-700 leading-relaxed">
              Our Service operates as a multi-tenant platform where each business maintains separate data. Business administrators can only view and manage data related to their own business. Customer data (WhatsApp conversations and appointments) is associated with the specific business they interact with.
            </p>
          </section>

          <section>
            <h2 className="text-2xl font-semibold text-gray-900 mt-8 mb-4">7. Data Retention</h2>
            <p className="text-gray-700 leading-relaxed">
              We retain conversation history and appointment data as long as necessary to provide the Service. Business administrators can request deletion of their data by contacting us. Conversation history is kept for service improvement and to provide context for ongoing appointments.
            </p>
          </section>

          <section>
            <h2 className="text-2xl font-semibold text-gray-900 mt-8 mb-4">8. Your Rights</h2>
            <p className="text-gray-700 leading-relaxed">You have the right to:</p>
            <ul className="list-disc pl-6 mt-2 space-y-2 text-gray-700">
              <li>Access your personal information</li>
              <li>Request correction of inaccurate data</li>
              <li>Request deletion of your data</li>
              <li>Opt-out of the Service by stopping WhatsApp interactions</li>
              <li>Revoke Google Calendar access at any time through the admin console</li>
            </ul>
          </section>

          <section>
            <h2 className="text-2xl font-semibold text-gray-900 mt-8 mb-4">9. Children&apos;s Privacy</h2>
            <p className="text-gray-700 leading-relaxed">
              Our Service is not intended for children under 13 years of age. We do not knowingly collect personal information from children under 13. If you are a parent or guardian and believe your child has provided us with personal information, please contact us.
            </p>
          </section>

          <section>
            <h2 className="text-2xl font-semibold text-gray-900 mt-8 mb-4">10. Changes to This Privacy Policy</h2>
            <p className="text-gray-700 leading-relaxed">
              We may update this Privacy Policy from time to time. We will notify you of any changes by posting the new Privacy Policy on this page and updating the &quot;Last updated&quot; date.
            </p>
          </section>

          <section>
            <h2 className="text-2xl font-semibold text-gray-900 mt-8 mb-4">11. International Data Transfers</h2>
            <p className="text-gray-700 leading-relaxed">
              Our Service is hosted and operated in the United States. If you are accessing the Service from outside the United States, please be aware that your information may be transferred to, stored, and processed in the United States where our servers are located.
            </p>
          </section>

          <section>
            <h2 className="text-2xl font-semibold text-gray-900 mt-8 mb-4">12. Contact Us</h2>
            <p className="text-gray-700 leading-relaxed">
              If you have any questions about this Privacy Policy, please contact us at:
            </p>
            <div className="mt-4 p-4 bg-gray-50 rounded-lg">
              <p className="text-gray-700"><strong>Email:</strong> privacy@yourdomain.com</p>
              <p className="text-gray-700 mt-2"><strong>Website:</strong> https://whats-app-bot-ten.vercel.app</p>
            </div>
          </section>
        </div>

        <div className="mt-12 pt-8 border-t border-gray-200 text-center">
          <Link
            href="/terms"
            className="text-blue-600 hover:text-blue-800 font-medium"
          >
            View Terms of Service →
          </Link>
        </div>
      </div>
    </div>
  )
}
