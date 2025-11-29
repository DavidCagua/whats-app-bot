"use client"

import { useState, useEffect } from "react"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Badge } from "@/components/ui/badge"
import { Switch } from "@/components/ui/switch"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Phone, Plus, Trash2, ExternalLink } from "lucide-react"
import { toast } from "sonner"
import {
  getWhatsAppNumbers,
  addWhatsAppNumber,
  deleteWhatsAppNumber,
  toggleWhatsAppNumberStatus,
  type WhatsAppNumber,
} from "@/lib/actions/whatsapp"

interface WhatsAppSettingsProps {
  businessId: string
}

export function WhatsAppSettings({ businessId }: WhatsAppSettingsProps) {
  const [numbers, setNumbers] = useState<WhatsAppNumber[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [showAddForm, setShowAddForm] = useState(false)
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const [numberToDelete, setNumberToDelete] = useState<string | null>(null)

  // Form state
  const [formData, setFormData] = useState({
    phoneNumberId: "",
    phoneNumber: "",
    displayName: "",
  })
  const [isSubmitting, setIsSubmitting] = useState(false)

  useEffect(() => {
    loadNumbers()
  }, [businessId])

  async function loadNumbers() {
    setIsLoading(true)
    const result = await getWhatsAppNumbers(businessId)
    if (result.success) {
      setNumbers(result.numbers as WhatsAppNumber[])
    } else if (result.error) {
      toast.error(result.error)
    }
    setIsLoading(false)
  }

  async function handleAddNumber(e: React.FormEvent) {
    e.preventDefault()
    setIsSubmitting(true)

    try {
      const result = await addWhatsAppNumber({
        businessId,
        phoneNumberId: formData.phoneNumberId.trim(),
        phoneNumber: formData.phoneNumber.trim(),
        displayName: formData.displayName.trim() || undefined,
      })

      if (result.success) {
        toast.success("WhatsApp number added successfully!")
        setFormData({ phoneNumberId: "", phoneNumber: "", displayName: "" })
        setShowAddForm(false)
        await loadNumbers()
      } else {
        toast.error(result.error || "Failed to add WhatsApp number")
      }
    } catch (error) {
      toast.error("An error occurred while adding the number")
      console.error(error)
    } finally {
      setIsSubmitting(false)
    }
  }

  async function handleDeleteNumber() {
    if (!numberToDelete) return

    try {
      const result = await deleteWhatsAppNumber(numberToDelete)
      if (result.success) {
        toast.success("WhatsApp number deleted successfully!")
        await loadNumbers()
      } else {
        toast.error(result.error || "Failed to delete WhatsApp number")
      }
    } catch (error) {
      toast.error("An error occurred while deleting the number")
      console.error(error)
    } finally {
      setDeleteDialogOpen(false)
      setNumberToDelete(null)
    }
  }

  async function handleToggleStatus(id: string) {
    try {
      const result = await toggleWhatsAppNumberStatus(id)
      if (result.success) {
        toast.success("Status updated successfully!")
        await loadNumbers()
      } else {
        toast.error(result.error || "Failed to update status")
      }
    } catch (error) {
      toast.error("An error occurred while updating the status")
      console.error(error)
    }
  }

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Phone className="h-5 w-5" />
            WhatsApp Configuration
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">Loading...</p>
        </CardContent>
      </Card>
    )
  }

  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Phone className="h-5 w-5" />
            WhatsApp Configuration
          </CardTitle>
          <CardDescription>
            Manage WhatsApp Business phone numbers for this business
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          {/* Help Documentation */}
          <div className="text-sm bg-blue-50 dark:bg-blue-950 p-4 rounded-lg space-y-2">
            <p className="font-semibold text-blue-900 dark:text-blue-100">
              Super Admin Instructions:
            </p>
            <ol className="list-decimal pl-4 space-y-1 text-blue-800 dark:text-blue-200">
              <li>Business owner provides their WhatsApp Business phone number</li>
              <li>Add their number to your Meta Business Manager account</li>
              <li>In Meta Business Manager, navigate to the phone number settings</li>
              <li>Copy the "Phone Number ID" (15-20 digit number, NOT the phone number itself)</li>
              <li>Paste both the Phone Number ID and display number below</li>
            </ol>
            <a
              href="https://developers.facebook.com/docs/whatsapp/business-management-api/manage-phone-numbers"
              target="_blank"
              rel="noopener noreferrer"
              className="text-blue-600 dark:text-blue-400 hover:underline inline-flex items-center gap-1 mt-2"
            >
              Meta Documentation
              <ExternalLink className="h-3 w-3" />
            </a>
          </div>

          {/* Existing Numbers */}
          {numbers.length > 0 && (
            <div className="space-y-3">
              <h3 className="text-sm font-semibold">Configured Numbers</h3>
              {numbers.map((number) => (
                <div
                  key={number.id}
                  className="flex items-center justify-between p-4 border rounded-lg"
                >
                  <div className="flex-1 space-y-1">
                    <div className="flex items-center gap-2">
                      <span className="font-medium">{number.phone_number}</span>
                      {number.display_name && (
                        <span className="text-sm text-muted-foreground">
                          ({number.display_name})
                        </span>
                      )}
                      {number.is_active ? (
                        <Badge variant="default" className="ml-2">
                          Active
                        </Badge>
                      ) : (
                        <Badge variant="secondary" className="ml-2">
                          Inactive
                        </Badge>
                      )}
                    </div>
                    <p className="text-xs text-muted-foreground">
                      ID: {number.phone_number_id}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <div className="flex items-center gap-2">
                      <Label htmlFor={`active-${number.id}`} className="text-sm">
                        Active
                      </Label>
                      <Switch
                        id={`active-${number.id}`}
                        checked={number.is_active}
                        onCheckedChange={() => handleToggleStatus(number.id)}
                      />
                    </div>
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() => {
                        setNumberToDelete(number.id)
                        setDeleteDialogOpen(true)
                      }}
                    >
                      <Trash2 className="h-4 w-4 text-destructive" />
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* Add Number Form */}
          {showAddForm ? (
            <form onSubmit={handleAddNumber} className="space-y-4 border p-4 rounded-lg">
              <div className="space-y-2">
                <Label htmlFor="phoneNumberId">
                  Phone Number ID <span className="text-destructive">*</span>
                </Label>
                <Input
                  id="phoneNumberId"
                  placeholder="e.g., 123456789012345"
                  value={formData.phoneNumberId}
                  onChange={(e) =>
                    setFormData({ ...formData, phoneNumberId: e.target.value })
                  }
                  required
                />
                <p className="text-xs text-muted-foreground">
                  The unique ID from Meta Business Manager (15-20 digits)
                </p>
              </div>

              <div className="space-y-2">
                <Label htmlFor="phoneNumber">
                  Display Phone Number <span className="text-destructive">*</span>
                </Label>
                <Input
                  id="phoneNumber"
                  placeholder="e.g., +573001234567"
                  value={formData.phoneNumber}
                  onChange={(e) => setFormData({ ...formData, phoneNumber: e.target.value })}
                  required
                />
                <p className="text-xs text-muted-foreground">
                  The actual phone number (with country code)
                </p>
              </div>

              <div className="space-y-2">
                <Label htmlFor="displayName">Display Name (Optional)</Label>
                <Input
                  id="displayName"
                  placeholder="e.g., Main Line"
                  value={formData.displayName}
                  onChange={(e) => setFormData({ ...formData, displayName: e.target.value })}
                />
                <p className="text-xs text-muted-foreground">
                  A friendly name to identify this number
                </p>
              </div>

              <div className="flex gap-2">
                <Button type="submit" disabled={isSubmitting}>
                  {isSubmitting ? "Adding..." : "Add Number"}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => {
                    setShowAddForm(false)
                    setFormData({ phoneNumberId: "", phoneNumber: "", displayName: "" })
                  }}
                >
                  Cancel
                </Button>
              </div>
            </form>
          ) : (
            <Button onClick={() => setShowAddForm(true)} className="w-full">
              <Plus className="h-4 w-4 mr-2" />
              Add WhatsApp Number
            </Button>
          )}

          {numbers.length === 0 && !showAddForm && (
            <div className="text-center py-8 text-muted-foreground">
              <Phone className="h-12 w-12 mx-auto mb-2 opacity-50" />
              <p className="text-sm">No WhatsApp numbers configured yet</p>
              <p className="text-xs mt-1">Add a number to enable WhatsApp bot for this business</p>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Delete Confirmation Dialog */}
      <AlertDialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete WhatsApp Number?</AlertDialogTitle>
            <AlertDialogDescription>
              This will remove the WhatsApp number from this business. The bot will no longer
              respond to messages sent to this number. This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel onClick={() => setNumberToDelete(null)}>
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction onClick={handleDeleteNumber} className="bg-destructive">
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
