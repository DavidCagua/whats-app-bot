"use client"

import { useState } from "react"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import * as z from "zod"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Separator } from "@/components/ui/separator"
import { Badge } from "@/components/ui/badge"
import { Plus, X, Save, Clock, MapPin, Phone, Globe, Users, CreditCard, Gift, UserCheck, Calendar, MessageSquare } from "lucide-react"
import { toast } from "sonner"
import { updateBusinessSettings, BusinessSettings } from "@/lib/actions/business-settings"

const businessSettingsSchema = z.object({
  name: z.string().min(1, "Business name is required"),
  business_type: z.string().min(1, "Business type is required"),
  address: z.string().min(1, "Address is required"),
  phone: z.string().min(1, "Phone is required"),
  city: z.string().min(1, "City is required"),
  state: z.string().min(1, "State is required"),
  country: z.string().min(1, "Country is required"),
  timezone: z.string().min(1, "Timezone is required"),
  language: z.string().min(1, "Language is required"),
  business_hours: z.object({
    monday: z.object({ open: z.string(), close: z.string() }),
    tuesday: z.object({ open: z.string(), close: z.string() }),
    wednesday: z.object({ open: z.string(), close: z.string() }),
    thursday: z.object({ open: z.string(), close: z.string() }),
    friday: z.object({ open: z.string(), close: z.string() }),
    saturday: z.object({ open: z.string(), close: z.string() }),
    sunday: z.object({ open: z.string(), close: z.string() }),
  }),
  services: z.array(z.object({
    name: z.string().min(1, "Service name is required"),
    price: z.number().min(0, "Price must be positive"),
    duration: z.number().min(1, "Duration must be at least 1 minute"),
  })),
  payment_methods: z.array(z.string()),
  promotions: z.array(z.string()),
  staff: z.array(z.object({
    name: z.string().min(1, "Staff name is required"),
    specialties: z.array(z.string()),
  })),
  appointment_settings: z.object({
    max_concurrent: z.number().min(1, "Must allow at least 1 concurrent appointment"),
    min_advance_hours: z.number().min(0, "Cannot be negative"),
    default_duration_minutes: z.number().min(1, "Must be at least 1 minute"),
  }),
  ai_prompt: z.string().min(1, "AI prompt is required"),
})

type BusinessSettingsFormData = z.infer<typeof businessSettingsSchema>

interface BusinessSettingsFormProps {
  business: {
    id: string
    name: string
    business_type: string | null
    settings: any
  }
  initialSettings: BusinessSettings
}

export function BusinessSettingsForm({ business, initialSettings }: BusinessSettingsFormProps) {
  const [isLoading, setIsLoading] = useState(false)

  const form = useForm<BusinessSettingsFormData>({
    resolver: zodResolver(businessSettingsSchema),
    defaultValues: initialSettings,
  })

  const onSubmit = async (data: BusinessSettingsFormData) => {
    setIsLoading(true)
    try {
      const result = await updateBusinessSettings(business.id, data)
      if (result.success) {
        toast.success("Settings updated successfully!")
      } else {
        toast.error(result.error || "Failed to update settings")
      }
    } catch (error) {
      toast.error("An error occurred while updating settings")
    } finally {
      setIsLoading(false)
    }
  }

  // Helper functions for dynamic arrays
  const addService = () => {
    const currentServices = form.getValues("services")
    form.setValue("services", [...currentServices, { name: "", price: 0, duration: 60 }])
  }

  const removeService = (index: number) => {
    const currentServices = form.getValues("services")
    form.setValue("services", currentServices.filter((_, i) => i !== index))
  }

  const addPaymentMethod = () => {
    const currentMethods = form.getValues("payment_methods")
    form.setValue("payment_methods", [...currentMethods, ""])
  }

  const removePaymentMethod = (index: number) => {
    const currentMethods = form.getValues("payment_methods")
    form.setValue("payment_methods", currentMethods.filter((_, i) => i !== index))
  }

  const addPromotion = () => {
    const currentPromotions = form.getValues("promotions")
    form.setValue("promotions", [...currentPromotions, ""])
  }

  const removePromotion = (index: number) => {
    const currentPromotions = form.getValues("promotions")
    form.setValue("promotions", currentPromotions.filter((_, i) => i !== index))
  }

  const addStaff = () => {
    const currentStaff = form.getValues("staff")
    form.setValue("staff", [...currentStaff, { name: "", specialties: [] }])
  }

  const removeStaff = (index: number) => {
    const currentStaff = form.getValues("staff")
    form.setValue("staff", currentStaff.filter((_, i) => i !== index))
  }

  const addStaffSpecialty = (staffIndex: number) => {
    const currentStaff = form.getValues("staff")
    const updatedStaff = [...currentStaff]
    updatedStaff[staffIndex].specialties.push("")
    form.setValue("staff", updatedStaff)
  }

  const removeStaffSpecialty = (staffIndex: number, specialtyIndex: number) => {
    const currentStaff = form.getValues("staff")
    const updatedStaff = [...currentStaff]
    updatedStaff[staffIndex].specialties = updatedStaff[staffIndex].specialties.filter((_, i) => i !== specialtyIndex)
    form.setValue("staff", updatedStaff)
  }

  return (
    <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-6">
      {/* Basic Information */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <MapPin className="h-5 w-5" />
            Basic Information
          </CardTitle>
          <CardDescription>
            Configure the basic details of your business
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="name">Business Name</Label>
              <Input
                id="name"
                {...form.register("name")}
                placeholder="Enter business name"
              />
              {form.formState.errors.name && (
                <p className="text-sm text-red-500">{form.formState.errors.name.message}</p>
              )}
            </div>

            <div className="space-y-2">
              <Label htmlFor="business_type">Business Type</Label>
              <Select
                value={form.watch("business_type")}
                onValueChange={(value) => form.setValue("business_type", value)}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Select business type" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="barberia">Barbería</SelectItem>
                  <SelectItem value="salon">Salón de Belleza</SelectItem>
                  <SelectItem value="spa">Spa</SelectItem>
                  <SelectItem value="clinic">Clínica</SelectItem>
                  <SelectItem value="restaurant">Restaurante</SelectItem>
                  <SelectItem value="other">Otro</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-2">
              <Label htmlFor="address">Address</Label>
              <Input
                id="address"
                {...form.register("address")}
                placeholder="Enter business address"
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="phone">Phone</Label>
              <Input
                id="phone"
                {...form.register("phone")}
                placeholder="+57 300 123 4567"
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="city">City</Label>
              <Input
                id="city"
                {...form.register("city")}
                placeholder="Enter city"
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="state">State/Province</Label>
              <Input
                id="state"
                {...form.register("state")}
                placeholder="Enter state or province"
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="country">Country</Label>
              <Input
                id="country"
                {...form.register("country")}
                placeholder="Enter country"
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="timezone">Timezone</Label>
              <Select
                value={form.watch("timezone")}
                onValueChange={(value) => form.setValue("timezone", value)}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Select timezone" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="America/Bogota">America/Bogota (Colombia)</SelectItem>
                  <SelectItem value="America/Mexico_City">America/Mexico_City (México)</SelectItem>
                  <SelectItem value="America/Argentina/Buenos_Aires">America/Argentina/Buenos_Aires (Argentina)</SelectItem>
                  <SelectItem value="America/Santiago">America/Santiago (Chile)</SelectItem>
                  <SelectItem value="America/Lima">America/Lima (Perú)</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-2">
              <Label htmlFor="language">Language</Label>
              <Select
                value={form.watch("language")}
                onValueChange={(value) => form.setValue("language", value)}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Select language" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="es-CO">Español (Colombia)</SelectItem>
                  <SelectItem value="es-MX">Español (México)</SelectItem>
                  <SelectItem value="es-AR">Español (Argentina)</SelectItem>
                  <SelectItem value="es-CL">Español (Chile)</SelectItem>
                  <SelectItem value="es-PE">Español (Perú)</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Business Hours */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Clock className="h-5 w-5" />
            Business Hours
          </CardTitle>
          <CardDescription>
            Set your business operating hours for each day of the week
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {Object.entries(form.watch("business_hours")).map(([day, hours]) => (
              <div key={day} className="space-y-2">
                <Label className="capitalize">{day}</Label>
                <div className="flex gap-2">
                  <Input
                    type="time"
                    value={hours.open}
                    onChange={(e) => {
                      const currentHours = form.getValues("business_hours")
                      form.setValue("business_hours", {
                        ...currentHours,
                        [day]: { ...hours, open: e.target.value }
                      })
                    }}
                    disabled={hours.open === "closed"}
                  />
                  <Input
                    type="time"
                    value={hours.close}
                    onChange={(e) => {
                      const currentHours = form.getValues("business_hours")
                      form.setValue("business_hours", {
                        ...currentHours,
                        [day]: { ...hours, close: e.target.value }
                      })
                    }}
                    disabled={hours.close === "closed"}
                  />
                </div>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    const currentHours = form.getValues("business_hours")
                    const isClosed = hours.open === "closed"
                    form.setValue("business_hours", {
                      ...currentHours,
                      [day]: isClosed 
                        ? { open: "09:00", close: "19:00" }
                        : { open: "closed", close: "closed" }
                    })
                  }}
                >
                  {hours.open === "closed" ? "Open" : "Closed"}
                </Button>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Services */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <CreditCard className="h-5 w-5" />
            Services
          </CardTitle>
          <CardDescription>
            Define the services you offer with their prices and durations
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            {form.watch("services").map((service, index) => (
              <div key={index} className="flex gap-4 items-end p-4 border rounded-lg">
                <div className="flex-1 space-y-2">
                  <Label>Service Name</Label>
                  <Input
                    {...form.register(`services.${index}.name`)}
                    placeholder="e.g., Corte de cabello"
                  />
                </div>
                <div className="w-32 space-y-2">
                  <Label>Price</Label>
                  <Input
                    type="number"
                    {...form.register(`services.${index}.price`, { valueAsNumber: true })}
                    placeholder="20000"
                  />
                </div>
                <div className="w-32 space-y-2">
                  <Label>Duration (min)</Label>
                  <Input
                    type="number"
                    {...form.register(`services.${index}.duration`, { valueAsNumber: true })}
                    placeholder="60"
                  />
                </div>
                <Button
                  type="button"
                  variant="outline"
                  size="icon"
                  onClick={() => removeService(index)}
                >
                  <X className="h-4 w-4" />
                </Button>
              </div>
            ))}
            <Button type="button" variant="outline" onClick={addService}>
              <Plus className="mr-2 h-4 w-4" />
              Add Service
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Payment Methods */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <CreditCard className="h-5 w-5" />
            Payment Methods
          </CardTitle>
          <CardDescription>
            List the payment methods you accept
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            {form.watch("payment_methods").map((method, index) => (
              <div key={index} className="flex gap-4 items-center">
                <Input
                  {...form.register(`payment_methods.${index}`)}
                  placeholder="e.g., Efectivo, Tarjeta, Nequi"
                />
                <Button
                  type="button"
                  variant="outline"
                  size="icon"
                  onClick={() => removePaymentMethod(index)}
                >
                  <X className="h-4 w-4" />
                </Button>
              </div>
            ))}
            <Button type="button" variant="outline" onClick={addPaymentMethod}>
              <Plus className="mr-2 h-4 w-4" />
              Add Payment Method
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Promotions */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Gift className="h-5 w-5" />
            Promotions
          </CardTitle>
          <CardDescription>
            Define special offers and promotions for your customers
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            {form.watch("promotions").map((promotion, index) => (
              <div key={index} className="flex gap-4 items-center">
                <Textarea
                  {...form.register(`promotions.${index}`)}
                  placeholder="e.g., Cumpleañero feliz: 10% de descuento si cumples este mes"
                  rows={2}
                />
                <Button
                  type="button"
                  variant="outline"
                  size="icon"
                  onClick={() => removePromotion(index)}
                >
                  <X className="h-4 w-4" />
                </Button>
              </div>
            ))}
            <Button type="button" variant="outline" onClick={addPromotion}>
              <Plus className="mr-2 h-4 w-4" />
              Add Promotion
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Staff */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Users className="h-5 w-5" />
            Staff
          </CardTitle>
          <CardDescription>
            Add your team members and their specialties
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            {form.watch("staff").map((staff, index) => (
              <div key={index} className="p-4 border rounded-lg space-y-4">
                <div className="flex gap-4 items-center">
                  <div className="flex-1">
                    <Label>Staff Name</Label>
                    <Input
                      {...form.register(`staff.${index}.name`)}
                      placeholder="e.g., Luis Gómez"
                    />
                  </div>
                  <Button
                    type="button"
                    variant="outline"
                    size="icon"
                    onClick={() => removeStaff(index)}
                  >
                    <X className="h-4 w-4" />
                  </Button>
                </div>
                <div className="space-y-2">
                  <Label>Specialties</Label>
                  <div className="space-y-2">
                    {staff.specialties.map((specialty, specialtyIndex) => (
                      <div key={specialtyIndex} className="flex gap-2 items-center">
                        <Input
                          value={specialty}
                          onChange={(e) => {
                            const currentStaff = form.getValues("staff")
                            const updatedStaff = [...currentStaff]
                            updatedStaff[index].specialties[specialtyIndex] = e.target.value
                            form.setValue("staff", updatedStaff)
                          }}
                          placeholder="e.g., Cortes clásicos, Fade"
                        />
                        <Button
                          type="button"
                          variant="outline"
                          size="icon"
                          onClick={() => removeStaffSpecialty(index, specialtyIndex)}
                        >
                          <X className="h-4 w-4" />
                        </Button>
                      </div>
                    ))}
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => addStaffSpecialty(index)}
                    >
                      <Plus className="mr-2 h-4 w-4" />
                      Add Specialty
                    </Button>
                  </div>
                </div>
              </div>
            ))}
            <Button type="button" variant="outline" onClick={addStaff}>
              <Plus className="mr-2 h-4 w-4" />
              Add Staff Member
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Appointment Settings */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Calendar className="h-5 w-5" />
            Appointment Settings
          </CardTitle>
          <CardDescription>
            Configure how appointments are managed
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="space-y-2">
              <Label htmlFor="max_concurrent">Max Concurrent Appointments</Label>
              <Input
                id="max_concurrent"
                type="number"
                {...form.register("appointment_settings.max_concurrent", { valueAsNumber: true })}
                placeholder="2"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="min_advance_hours">Min Advance Hours</Label>
              <Input
                id="min_advance_hours"
                type="number"
                {...form.register("appointment_settings.min_advance_hours", { valueAsNumber: true })}
                placeholder="1"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="default_duration_minutes">Default Duration (minutes)</Label>
              <Input
                id="default_duration_minutes"
                type="number"
                {...form.register("appointment_settings.default_duration_minutes", { valueAsNumber: true })}
                placeholder="60"
              />
            </div>
          </div>
        </CardContent>
      </Card>

      {/* AI Prompt */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <MessageSquare className="h-5 w-5" />
            AI Assistant Prompt
          </CardTitle>
          <CardDescription>
            Customize how your AI assistant communicates with customers
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="space-y-2">
            <Label htmlFor="ai_prompt">AI Prompt</Label>
            <Textarea
              id="ai_prompt"
              {...form.register("ai_prompt")}
              placeholder="Enter the AI prompt for your business..."
              rows={10}
              className="font-mono text-sm"
            />
            {form.formState.errors.ai_prompt && (
              <p className="text-sm text-red-500">{form.formState.errors.ai_prompt.message}</p>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Submit Button */}
      <div className="flex justify-end">
        <Button type="submit" disabled={isLoading}>
          <Save className="mr-2 h-4 w-4" />
          {isLoading ? "Saving..." : "Save Settings"}
        </Button>
      </div>
    </form>
  )
}
