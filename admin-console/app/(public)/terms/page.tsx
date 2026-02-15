import Link from "next/link"

export default function TermsOfServicePage() {
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
          <h1 className="text-4xl font-bold text-gray-900 mb-4">Terms of Service</h1>
          <p className="text-sm text-gray-600">Last updated: {new Date().toLocaleDateString()}</p>
        </div>

        <div className="prose prose-gray max-w-none space-y-6">
          <section>
            <h2 className="text-2xl font-semibold text-gray-900 mt-8 mb-4">1. Agreement to Terms</h2>
            <p className="text-gray-700 leading-relaxed">
              By accessing or using our WhatsApp Bot service (&quot;Service&quot;), you agree to be bound by these Terms of Service (&quot;Terms&quot;). If you disagree with any part of these terms, you may not access the Service.
            </p>
          </section>

          <section>
            <h2 className="text-2xl font-semibold text-gray-900 mt-8 mb-4">2. Description of Service</h2>
            <p className="text-gray-700 leading-relaxed">
              Our Service provides a multi-tenant WhatsApp bot platform that enables businesses to manage customer appointments through automated conversations. The Service includes:
            </p>
            <ul className="list-disc pl-6 mt-2 space-y-2 text-gray-700">
              <li>WhatsApp integration for customer communication</li>
              <li>AI-powered natural language processing for appointment scheduling</li>
              <li>Google Calendar integration for appointment management</li>
              <li>Admin console for business configuration and management</li>
              <li>Multi-tenant architecture supporting multiple businesses</li>
            </ul>
          </section>

          <section>
            <h2 className="text-2xl font-semibold text-gray-900 mt-8 mb-4">3. User Accounts and Businesses</h2>

            <h3 className="text-xl font-semibold text-gray-800 mt-6 mb-3">3.1 Business Accounts</h3>
            <p className="text-gray-700 leading-relaxed">
              Business administrators must create an account to access the admin console. You are responsible for:
            </p>
            <ul className="list-disc pl-6 mt-2 space-y-2 text-gray-700">
              <li>Maintaining the confidentiality of your account credentials</li>
              <li>All activities that occur under your account</li>
              <li>Notifying us immediately of any unauthorized access</li>
              <li>Providing accurate and current business information</li>
            </ul>

            <h3 className="text-xl font-semibold text-gray-800 mt-6 mb-3">3.2 End Users</h3>
            <p className="text-gray-700 leading-relaxed">
              Customers who interact with your business through WhatsApp are subject to these Terms. By using the WhatsApp bot, end users agree to the automated processing of their messages and appointment information.
            </p>
          </section>

          <section>
            <h2 className="text-2xl font-semibold text-gray-900 mt-8 mb-4">4. Acceptable Use Policy</h2>
            <p className="text-gray-700 leading-relaxed">You agree NOT to use the Service to:</p>
            <ul className="list-disc pl-6 mt-2 space-y-2 text-gray-700">
              <li>Violate any laws or regulations</li>
              <li>Send spam, unsolicited messages, or harass users</li>
              <li>Impersonate any person or entity</li>
              <li>Transmit malicious code or viruses</li>
              <li>Interfere with or disrupt the Service</li>
              <li>Attempt to gain unauthorized access to the Service or other accounts</li>
              <li>Use the Service for any illegal or unauthorized purpose</li>
              <li>Violate WhatsApp&apos;s Business Policy or Terms of Service</li>
            </ul>
          </section>

          <section>
            <h2 className="text-2xl font-semibold text-gray-900 mt-8 mb-4">5. Third-Party Services</h2>
            <p className="text-gray-700 leading-relaxed">
              The Service integrates with third-party services including WhatsApp (Meta), Google Calendar, and OpenAI. Your use of these third-party services is subject to their respective terms and policies:
            </p>
            <ul className="list-disc pl-6 mt-2 space-y-2 text-gray-700">
              <li>WhatsApp Business Terms: <a href="https://www.whatsapp.com/legal/business-terms" className="text-blue-600 hover:underline">https://www.whatsapp.com/legal/business-terms</a></li>
              <li>Google Terms of Service: <a href="https://policies.google.com/terms" className="text-blue-600 hover:underline">https://policies.google.com/terms</a></li>
              <li>OpenAI Terms: <a href="https://openai.com/policies/terms-of-use" className="text-blue-600 hover:underline">https://openai.com/policies/terms-of-use</a></li>
            </ul>
          </section>

          <section>
            <h2 className="text-2xl font-semibold text-gray-900 mt-8 mb-4">6. Google Calendar Access</h2>
            <p className="text-gray-700 leading-relaxed">
              When you authorize our Service to access your Google Calendar:
            </p>
            <ul className="list-disc pl-6 mt-2 space-y-2 text-gray-700">
              <li>We will only access calendars you explicitly authorize</li>
              <li>We will create, read, update, and delete calendar events as needed for appointment scheduling</li>
              <li>Your credentials are encrypted and stored securely</li>
              <li>You can revoke access at any time through the admin console</li>
              <li>Revoking access will disable appointment scheduling features</li>
            </ul>
          </section>

          <section>
            <h2 className="text-2xl font-semibold text-gray-900 mt-8 mb-4">7. Service Availability</h2>
            <p className="text-gray-700 leading-relaxed">
              We strive to maintain high availability but do not guarantee uninterrupted service. The Service may be unavailable due to:
            </p>
            <ul className="list-disc pl-6 mt-2 space-y-2 text-gray-700">
              <li>Scheduled maintenance</li>
              <li>Third-party service outages (WhatsApp, Google, OpenAI)</li>
              <li>Technical issues or system failures</li>
              <li>Force majeure events</li>
            </ul>
            <p className="text-gray-700 leading-relaxed mt-4">
              We are not liable for any damages resulting from service interruptions.
            </p>
          </section>

          <section>
            <h2 className="text-2xl font-semibold text-gray-900 mt-8 mb-4">8. Data and Privacy</h2>
            <p className="text-gray-700 leading-relaxed">
              Your use of the Service is also governed by our Privacy Policy. We collect and process data as described in the Privacy Policy. By using the Service, you consent to such processing and warrant that all data provided is accurate.
            </p>
          </section>

          <section>
            <h2 className="text-2xl font-semibold text-gray-900 mt-8 mb-4">9. Intellectual Property</h2>
            <p className="text-gray-700 leading-relaxed">
              The Service and its original content, features, and functionality are owned by us and are protected by international copyright, trademark, patent, trade secret, and other intellectual property laws.
            </p>
            <p className="text-gray-700 leading-relaxed mt-4">
              You retain ownership of your business data, customer conversations, and appointment information. By using the Service, you grant us a license to use this data solely for providing and improving the Service.
            </p>
          </section>

          <section>
            <h2 className="text-2xl font-semibold text-gray-900 mt-8 mb-4">10. Limitation of Liability</h2>
            <p className="text-gray-700 leading-relaxed">
              To the maximum extent permitted by law, we shall not be liable for any indirect, incidental, special, consequential, or punitive damages, including without limitation, loss of profits, data, use, goodwill, or other intangible losses resulting from:
            </p>
            <ul className="list-disc pl-6 mt-2 space-y-2 text-gray-700">
              <li>Your access to or use of or inability to access or use the Service</li>
              <li>Any conduct or content of any third party on the Service</li>
              <li>Unauthorized access, use, or alteration of your transmissions or content</li>
              <li>Missed appointments or scheduling errors</li>
              <li>AI-generated responses that are inaccurate or inappropriate</li>
            </ul>
          </section>

          <section>
            <h2 className="text-2xl font-semibold text-gray-900 mt-8 mb-4">11. Disclaimer of Warranties</h2>
            <p className="text-gray-700 leading-relaxed">
              The Service is provided &quot;AS IS&quot; and &quot;AS AVAILABLE&quot; without warranties of any kind, whether express or implied. We do not warrant that:
            </p>
            <ul className="list-disc pl-6 mt-2 space-y-2 text-gray-700">
              <li>The Service will be uninterrupted, secure, or error-free</li>
              <li>The results obtained from the Service will be accurate or reliable</li>
              <li>The AI-generated responses will always be appropriate or correct</li>
              <li>Any errors in the Service will be corrected</li>
            </ul>
          </section>

          <section>
            <h2 className="text-2xl font-semibold text-gray-900 mt-8 mb-4">12. Indemnification</h2>
            <p className="text-gray-700 leading-relaxed">
              You agree to indemnify and hold harmless the Service, its affiliates, and their respective officers, directors, employees, and agents from any claims, damages, losses, liabilities, and expenses (including legal fees) arising from:
            </p>
            <ul className="list-disc pl-6 mt-2 space-y-2 text-gray-700">
              <li>Your use of the Service</li>
              <li>Your violation of these Terms</li>
              <li>Your violation of any rights of another party</li>
              <li>Your business&apos;s interactions with customers through the Service</li>
            </ul>
          </section>

          <section>
            <h2 className="text-2xl font-semibold text-gray-900 mt-8 mb-4">13. Termination</h2>
            <p className="text-gray-700 leading-relaxed">
              We may terminate or suspend your account and access to the Service immediately, without prior notice, for any reason, including:
            </p>
            <ul className="list-disc pl-6 mt-2 space-y-2 text-gray-700">
              <li>Breach of these Terms</li>
              <li>Violation of applicable laws or third-party rights</li>
              <li>Request from law enforcement or government agencies</li>
              <li>Suspected fraud or security concerns</li>
            </ul>
            <p className="text-gray-700 leading-relaxed mt-4">
              Upon termination, your right to use the Service will immediately cease. We may delete your data according to our data retention policies.
            </p>
          </section>

          <section>
            <h2 className="text-2xl font-semibold text-gray-900 mt-8 mb-4">14. Changes to Terms</h2>
            <p className="text-gray-700 leading-relaxed">
              We reserve the right to modify or replace these Terms at any time. We will provide notice of significant changes by posting a notice on the Service or sending an email. Your continued use of the Service after changes become effective constitutes acceptance of the new Terms.
            </p>
          </section>

          <section>
            <h2 className="text-2xl font-semibold text-gray-900 mt-8 mb-4">15. Governing Law</h2>
            <p className="text-gray-700 leading-relaxed">
              These Terms shall be governed by and construed in accordance with the laws of the Republic of Colombia, without regard to its conflict of law provisions.
            </p>
          </section>

          <section>
            <h2 className="text-2xl font-semibold text-gray-900 mt-8 mb-4">16. Dispute Resolution</h2>
            <p className="text-gray-700 leading-relaxed">
              Any disputes arising out of or relating to these Terms or the Service shall be resolved through binding arbitration, except that either party may seek injunctive relief in court for intellectual property infringement or violation of confidentiality obligations.
            </p>
          </section>

          <section>
            <h2 className="text-2xl font-semibold text-gray-900 mt-8 mb-4">17. Severability</h2>
            <p className="text-gray-700 leading-relaxed">
              If any provision of these Terms is held to be invalid or unenforceable, the remaining provisions will remain in full force and effect.
            </p>
          </section>

          <section>
            <h2 className="text-2xl font-semibold text-gray-900 mt-8 mb-4">18. Contact Information</h2>
            <p className="text-gray-700 leading-relaxed">
              If you have any questions about these Terms, please contact us at:
            </p>
            <div className="mt-4 p-4 bg-gray-50 rounded-lg">
              <p className="text-gray-700"><strong>Email:</strong> <a href="mailto:dfcaguazango@gmail.com" className="text-blue-600 hover:underline">dfcaguazango@gmail.com</a></p>
              <p className="text-gray-700 mt-2"><strong>Website:</strong> https://whats-app-bot-ten.vercel.app</p>
            </div>
          </section>
        </div>

        <div className="mt-12 pt-8 border-t border-gray-200 text-center">
          <Link
            href="/privacy"
            className="text-blue-600 hover:text-blue-800 font-medium"
          >
            View Privacy Policy →
          </Link>
        </div>
      </div>
    </div>
  )
}
