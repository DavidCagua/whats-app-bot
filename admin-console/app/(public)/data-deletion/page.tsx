import Link from "next/link"

export default function DataDeletionPage() {
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
          <h1 className="text-4xl font-bold text-gray-900 mb-4">Data Deletion Instructions</h1>
          <p className="text-sm text-gray-600">How to request deletion of your data from our Service</p>
        </div>

        <div className="prose prose-gray max-w-none space-y-6">
          <section>
            <p className="text-gray-700 leading-relaxed">
              If you have used our WhatsApp Bot service (&quot;Service&quot;) and want to request deletion of your data, you can do so at any time. We provide the following options depending on how you interacted with the Service.
            </p>
          </section>

          <section>
            <h2 className="text-2xl font-semibold text-gray-900 mt-8 mb-4">If you chatted with a business via WhatsApp</h2>
            <p className="text-gray-700 leading-relaxed">
              If you are an end user who communicated with a business through our WhatsApp bot (e.g. to book an appointment):
            </p>
            <ul className="list-disc pl-6 mt-2 space-y-2 text-gray-700">
              <li>Send an email to <a href="mailto:dfcaguazango@gmail.com" className="text-blue-600 hover:underline">dfcaguazango@gmail.com</a> with the subject &quot;Data deletion request&quot;.</li>
              <li>Include the phone number you used on WhatsApp (with country code, e.g. +57 300 123 4567).</li>
              <li>Optionally, tell us the business name if you remember it so we can locate your data faster.</li>
            </ul>
            <p className="text-gray-700 leading-relaxed mt-4">
              We will delete your conversation history and any stored appointment data linked to that number. This does not affect data held by WhatsApp (Meta), which is governed by their privacy policy.
            </p>
          </section>

          <section>
            <h2 className="text-2xl font-semibold text-gray-900 mt-8 mb-4">If you are a business administrator</h2>
            <p className="text-gray-700 leading-relaxed">
              If you have an admin account and want to delete your business data or your user account:
            </p>
            <ul className="list-disc pl-6 mt-2 space-y-2 text-gray-700">
              <li>Send an email to <a href="mailto:dfcaguazango@gmail.com" className="text-blue-600 hover:underline">dfcaguazango@gmail.com</a> with the subject &quot;Account / business data deletion request&quot;.</li>
              <li>Include the email address associated with your admin account and, if applicable, the business name.</li>
              <li>Specify whether you want to delete only certain data (e.g. Google Calendar connection) or your full account and business data.</li>
            </ul>
            <p className="text-gray-700 leading-relaxed mt-4">
              We will delete your account, business configuration, encrypted calendar credentials, and any associated conversation and appointment data as requested.
            </p>
          </section>

          <section>
            <h2 className="text-2xl font-semibold text-gray-900 mt-8 mb-4">What we delete</h2>
            <p className="text-gray-700 leading-relaxed">When you request deletion, we will remove:</p>
            <ul className="list-disc pl-6 mt-2 space-y-2 text-gray-700">
              <li>Conversation history linked to your phone number or business</li>
              <li>Stored appointment data</li>
              <li>Account credentials and business settings (for admin accounts)</li>
              <li>Google Calendar connection and stored tokens (for business accounts)</li>
            </ul>
            <p className="text-gray-700 leading-relaxed mt-4">
              We process deletion requests within 30 days. You will receive a confirmation email once the deletion is complete. Some data may be retained where required by law or for legitimate operational purposes (e.g. backup retention for a limited period).
            </p>
          </section>

          <section>
            <h2 className="text-2xl font-semibold text-gray-900 mt-8 mb-4">Contact</h2>
            <p className="text-gray-700 leading-relaxed">
              For any questions about data deletion or your privacy, contact us at{" "}
              <a href="mailto:dfcaguazango@gmail.com" className="text-blue-600 hover:underline">dfcaguazango@gmail.com</a>.
            </p>
          </section>
        </div>

        <div className="mt-12 pt-8 border-t border-gray-200 flex justify-center gap-6">
          <Link href="/privacy" className="text-blue-600 hover:text-blue-800 font-medium">
            Privacy Policy →
          </Link>
          <Link href="/terms" className="text-blue-600 hover:text-blue-800 font-medium">
            Terms of Service →
          </Link>
        </div>
      </div>
    </div>
  )
}
