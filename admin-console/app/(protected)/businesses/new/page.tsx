"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import * as z from "zod"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { ArrowLeft, Building2 } from "lucide-react"
import { toast } from "sonner"
import Link from "next/link"
import { createBusiness } from "@/lib/actions/business"

const createBusinessSchema = z.object({
  name: z.string().min(1, "Business name is required"),
  business_type: z.string().min(1, "Business type is required"),
})

type CreateBusinessFormData = z.infer<typeof createBusinessSchema>

export default function NewBusinessPage() {
  const router = useRouter()
  const [isLoading, setIsLoading] = useState(false)

  const form = useForm<CreateBusinessFormData>({
    resolver: zodResolver(createBusinessSchema),
    defaultValues: {
      name: "",
      business_type: "barberia",
    },
  })

  const onSubmit = async (data: CreateBusinessFormData) => {
    setIsLoading(true)
    try {
      const result = await createBusiness(data)
      if (result.success && result.businessId) {
        toast.success("Business created successfully!")
        router.push(`/businesses/${result.businessId}/settings`)
      } else {
        toast.error(result.error || "Failed to create business")
      }
    } catch {
      toast.error("An error occurred while creating the business")
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-4">
        <Button variant="ghost" size="icon" asChild>
          <Link href="/businesses">
            <ArrowLeft className="h-4 w-4" />
          </Link>
        </Button>
        <div>
          <h1 className="text-3xl font-bold">Create Business</h1>
          <p className="text-muted-foreground">
            Add a new business to the platform
          </p>
        </div>
      </div>

      <Card className="max-w-2xl">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Building2 className="h-5 w-5" />
            Business Details
          </CardTitle>
          <CardDescription>
            Enter the basic information for the new business
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
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
                  <SelectItem value="barberia">Barberia</SelectItem>
                  <SelectItem value="salon">Salon de Belleza</SelectItem>
                  <SelectItem value="spa">Spa</SelectItem>
                  <SelectItem value="clinic">Clinica</SelectItem>
                  <SelectItem value="restaurant">Restaurante</SelectItem>
                  <SelectItem value="other">Otro</SelectItem>
                </SelectContent>
              </Select>
              {form.formState.errors.business_type && (
                <p className="text-sm text-red-500">{form.formState.errors.business_type.message}</p>
              )}
            </div>

            <div className="flex justify-end gap-4 pt-4">
              <Button variant="outline" asChild>
                <Link href="/businesses">Cancel</Link>
              </Button>
              <Button type="submit" disabled={isLoading}>
                {isLoading ? "Creating..." : "Create Business"}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
