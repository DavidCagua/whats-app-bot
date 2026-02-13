"use client"

import { useState, useEffect } from "react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Calendar, Link2, Unlink, Loader2, CheckCircle2, XCircle, Mail } from "lucide-react"
import { toast } from "sonner"
import { getCalendarStatus, disconnectGoogleCalendar, type CalendarStatus } from "@/lib/actions/calendar"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"

interface GoogleCalendarSettingsProps {
  businessId: string
  readOnly?: boolean
  initialConnected?: boolean
  showSuccessMessage?: boolean
  errorMessage?: string
}

export function GoogleCalendarSettings({
  businessId,
  readOnly = false,
  initialConnected = false,
  showSuccessMessage = false,
  errorMessage,
}: GoogleCalendarSettingsProps) {
  const [status, setStatus] = useState<CalendarStatus>({
    is_configured: initialConnected,
  })
  const [isLoading, setIsLoading] = useState(true)
  const [isDisconnecting, setIsDisconnecting] = useState(false)

  useEffect(() => {
    async function fetchStatus() {
      try {
        const calendarStatus = await getCalendarStatus(businessId)
        setStatus(calendarStatus)
      } catch (err) {
        console.error("Error fetching calendar status:", err)
      } finally {
        setIsLoading(false)
      }
    }
    fetchStatus()
  }, [businessId])

  useEffect(() => {
    if (showSuccessMessage) {
      toast.success("Google Calendar connected successfully!")
    }
    if (errorMessage) {
      toast.error(`Failed to connect calendar: ${errorMessage}`)
    }
  }, [showSuccessMessage, errorMessage])

  const handleConnect = () => {
    // Redirect to OAuth flow
    window.location.href = `/api/calendar/connect?businessId=${businessId}`
  }

  const handleDisconnect = async () => {
    setIsDisconnecting(true)
    try {
      const result = await disconnectGoogleCalendar(businessId)
      if (result.success) {
        toast.success("Google Calendar disconnected")
        setStatus({ is_configured: false })
      } else {
        toast.error(result.error || "Failed to disconnect calendar")
      }
    } catch {
      toast.error("An error occurred")
    } finally {
      setIsDisconnecting(false)
    }
  }

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Calendar className="h-5 w-5" />
            Google Calendar Integration
          </CardTitle>
        </CardHeader>
        <CardContent className="flex items-center justify-center py-8">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Calendar className="h-5 w-5" />
          Google Calendar Integration
        </CardTitle>
        <CardDescription>
          Connect your Google Calendar to enable appointment scheduling via WhatsApp
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {status.is_configured ? (
          <>
            <div className="flex items-center gap-3 p-4 bg-green-50 dark:bg-green-950 border border-green-200 dark:border-green-800 rounded-lg">
              <CheckCircle2 className="h-5 w-5 text-green-600 shrink-0" />
              <div className="flex-1 min-w-0">
                <p className="font-medium text-green-900 dark:text-green-100">
                  Calendar Connected
                </p>
                <div className="flex items-center gap-2 mt-1 text-sm text-green-700 dark:text-green-300">
                  <Mail className="h-4 w-4 shrink-0" />
                  <span className="font-medium">Connected account:</span>
                  <span className="truncate" title={status.connected_email || undefined}>
                    {status.connected_email || "Unknown (connected before this was tracked)"}
                  </span>
                </div>
              </div>
              <Badge variant="secondary" className="bg-green-100 text-green-800 shrink-0">
                Active
              </Badge>
            </div>

            {status.calendar_id && (
              <div className="text-sm text-muted-foreground">
                Calendar ID: <code className="bg-muted px-1 rounded">{status.calendar_id}</code>
              </div>
            )}

            {!readOnly && (
              <AlertDialog>
                <AlertDialogTrigger asChild>
                  <Button variant="outline" className="text-destructive">
                    <Unlink className="mr-2 h-4 w-4" />
                    Disconnect Calendar
                  </Button>
                </AlertDialogTrigger>
                <AlertDialogContent>
                  <AlertDialogHeader>
                    <AlertDialogTitle>Disconnect Google Calendar?</AlertDialogTitle>
                    <AlertDialogDescription>
                      This will remove the calendar connection. WhatsApp appointment scheduling
                      will no longer work until you reconnect a calendar.
                    </AlertDialogDescription>
                  </AlertDialogHeader>
                  <AlertDialogFooter>
                    <AlertDialogCancel>Cancel</AlertDialogCancel>
                    <AlertDialogAction
                      onClick={handleDisconnect}
                      disabled={isDisconnecting}
                      className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                    >
                      {isDisconnecting ? "Disconnecting..." : "Disconnect"}
                    </AlertDialogAction>
                  </AlertDialogFooter>
                </AlertDialogContent>
              </AlertDialog>
            )}
          </>
        ) : (
          <>
            <div className="flex items-center gap-3 p-4 bg-muted border rounded-lg">
              <XCircle className="h-5 w-5 text-muted-foreground" />
              <div className="flex-1">
                <p className="font-medium">No Calendar Connected</p>
                <p className="text-sm text-muted-foreground">
                  Connect a Google Calendar to enable appointment scheduling
                </p>
              </div>
            </div>

            {!readOnly && (
              <Button onClick={handleConnect}>
                <Link2 className="mr-2 h-4 w-4" />
                Connect Google Calendar
              </Button>
            )}
          </>
        )}

        <div className="text-sm text-muted-foreground pt-2 border-t">
          <p className="font-medium mb-1">How it works:</p>
          <ul className="list-disc list-inside space-y-1">
            <li>Connect your Google Calendar account</li>
            <li>Customers can book appointments via WhatsApp</li>
            <li>Appointments appear directly in your calendar</li>
            <li>The bot checks availability before booking</li>
          </ul>
        </div>
      </CardContent>
    </Card>
  )
}
