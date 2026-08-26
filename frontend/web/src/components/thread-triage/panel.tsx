import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"

export const Panel = ({
  title,
  children,
}: {
  title: string
  children: React.ReactNode
}) => {
  return (
    <Card>
      <CardHeader className="pb-0">
        <CardTitle className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
          {title}
        </CardTitle>
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  )
}

export const Field = ({
  label,
  value,
}: {
  label: string
  value: React.ReactNode
}) => {
  return (
    <div>
      <p className="text-xs text-muted-foreground">{label}</p>
      <div className="mt-0.5 text-sm text-gray-900 dark:text-gray-100">{value}</div>
    </div>
  )
}
