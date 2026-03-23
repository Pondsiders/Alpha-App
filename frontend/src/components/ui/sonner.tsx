import { Toaster as Sonner, type ToasterProps } from "sonner";

const Toaster = ({ ...props }: ToasterProps) => {
  return (
    <Sonner
      theme="dark"
      className="toaster group"
      toastOptions={{
        classNames: {
          toast:
            "group toast group-[.toaster]:bg-surface group-[.toaster]:text-foreground group-[.toaster]:border-border group-[.toaster]:shadow-lg",
          description: "group-[.toast]:text-muted",
          actionButton:
            "group-[.toast]:bg-primary group-[.toast]:text-primary-foreground",
          cancelButton:
            "group-[.toast]:bg-muted group-[.toast]:text-muted-foreground",
          error:
            "group-[.toaster]:!bg-[#1a0d0c] group-[.toaster]:!border-[#C4504A]/40 group-[.toaster]:!text-[#C4504A]",
          warning:
            "group-[.toaster]:!bg-[#1a150c] group-[.toaster]:!border-[#F5A623]/40 group-[.toaster]:!text-[#F5A623]",
        },
      }}
      {...props}
    />
  );
};

export { Toaster };
