import { useState, useMemo, useEffect, useLayoutEffect, useRef } from 'react'
import { useRegisterPageChatContext } from '@/lib/page-chat-context'
import { getAccountName } from '@/lib/account-utils'
import { AccountIcon } from '@/components/account-icon'
import { currentMonth, monthRange, monthFromRange } from '@/lib/month-utils'
import { MonthStepper } from '@/components/month-stepper'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useDisplayLocale, useDateLocale } from '@/hooks/use-display-locale'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { transactions, categories as categoriesApi, categoryGroups as categoryGroupsApi, accounts as accountsApi, recurring, payees as payeesApi, admin, groups as groupsApi, rules as rulesApi } from '@/lib/api'
import { invalidateFinancialQueries } from '@/lib/invalidate-queries'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Skeleton } from '@/components/ui/skeleton'
import { AlertTriangle, ArrowLeftRight, ArrowRight, ArrowUp, ArrowDown, Check, ChevronDown, Copy, Download, Info, MoreHorizontal, Paperclip, Repeat, RotateCcw, Users, X, EyeClosed, SlidersHorizontal } from 'lucide-react'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import type { Transaction, Rule } from '@/types'
import { RuleDialog, type RuleDialogInitialData } from '@/components/rule-dialog'
import { PageHeader } from '@/components/page-header'
import { calculateRangeSelection } from '@/lib/selection-utils'
import { CategoryIcon } from '@/components/category-icon'
import { CategorySelect } from '@/components/category-select'
import { TransactionDialog, extractApiError, type SaveAction } from '@/components/transaction-dialog'
import { TransactionsColumnPicker } from '@/components/transactions-column-picker'
import { DESCRIPTION_MIN_WIDTH, DESCRIPTION_MAX_WIDTH, FLEXIBLE_COL_ID, type ColumnDef, type ColumnId, useTransactionsGridState } from '@/components/transactions-grid-columns'
import { TransferDialog } from '@/components/transfer-dialog'
import { LinkTransferDialog } from '@/components/link-transfer-dialog'
import { BulkAddToGroupDialog, type BulkAddToGroupSubmission } from '@/components/bulk-add-to-group-dialog'
import { TransactionsFilterBar } from '@/components/transactions-filter-bar'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useAuth } from '@/contexts/auth-context'
import { useWorkspace } from '@/contexts/workspace-context'
import { useCollectionFilter } from '@/contexts/collection-filter-context'

type TransactionUpdatePayload = Partial<Transaction> & {
  apply_to_transfer_pair?: boolean
}

/** A transfer pair merged into a single "Origem ➔ Destino" row (All Accounts). */
type TransferPairRow = {
  kind: 'transfer-pair'
  key: string
  pairId: string
  legIds: [string, string]
  debit: Transaction
  credit: Transaction
  shared: boolean
}

type DisplayRow = (Transaction & { kind?: 'transaction' }) | TransferPairRow

function formatCurrency(value: number, currency = 'USD', locale = 'en-US') {
  return new Intl.NumberFormat(locale, { style: 'currency', currency }).format(value)
}

function parseHashtags(notes: string | null): string[] {
  if (!notes) return []
  const matches = notes.match(/#[\w\u00C0-\u017E-]+/g)
  return matches ?? []
}

export default function TransactionsPage() {
  const { t, i18n } = useTranslation()
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const { mask } = usePrivacyMode()
  const { user } = useAuth()
  const { activeAccountIds } = useCollectionFilter()
  const { canWrite } = useWorkspace()
  const userCurrency = user?.preferences?.currency_display ?? 'USD'
  const queryClient = useQueryClient()
  const [page, setPage] = useState(1)
  const [limit, setLimit] = useState<number>(() => {
    try {
      const stored = localStorage.getItem('talisma.transactions.pageSize')
      return stored ? Number(stored) : 20
    } catch {
      return 20
    }
  })
  const [filterAccountIds, setFilterAccountIds] = useState<string[]>([])
  const [filterCategoryIds, setFilterCategoryIds] = useState<string[]>(() => {
    const initial = searchParams.get('category_id')
    return initial ? [initial] : []
  })
  const [filterUncategorized, setFilterUncategorized] = useState<boolean>(false)
  // Seed the date range from the URL, or default to the current month on first
  // open (no ?from/?to). Done in the initializer so it survives effect re-runs
  // (e.g. React StrictMode's double-invoke in development).
  const [filterFrom, setFilterFrom] = useState<string>(() => {
    const f = searchParams.get('from')
    const t = searchParams.get('to')
    return f || t ? (f ?? '') : monthRange(currentMonth()).from
  })
  const [filterTo, setFilterTo] = useState<string>(() => {
    const f = searchParams.get('from')
    const t = searchParams.get('to')
    return f || t ? (t ?? '') : monthRange(currentMonth()).to
  })
  // Month reflected by the stepper: the active range when it spans exactly one
  // full month, otherwise the current month (custom ranges still navigable).
  const steppedMonth = monthFromRange(filterFrom, filterTo) ?? currentMonth()
  const handleMonthChange = (ym: string) => {
    const { from, to } = monthRange(ym)
    setFilterFrom(from)
    setFilterTo(to)
    setPage(1)
  }
  const [searchInput, setSearchInput] = useState(() => searchParams.get('q') ?? '')
  const [searchQuery, setSearchQuery] = useState(() => searchParams.get('q') ?? '')
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingTx, setEditingTx] = useState<Transaction | null>(null)
  const [formResetKey, setFormResetKey] = useState(0)
  const [duplicateDraft, setDuplicateDraft] = useState<Partial<Transaction> | null>(null)
  const [filterPayee, setFilterPayee] = useState<string>(searchParams.get('payee_id') ?? '')
  const [filterGroupId, setFilterGroupId] = useState<string>(searchParams.get('group_id') ?? '')
  const [filterType, setFilterType] = useState<string>(searchParams.get('type') ?? '')
  const [filterMinAmount, setFilterMinAmount] = useState<string>(searchParams.get('min_amount') ?? '')
  const [filterMaxAmount, setFilterMaxAmount] = useState<string>(searchParams.get('max_amount') ?? '')
  const [tagFilters, setTagFilters] = useState<string[]>([])

  // When the page is opened with a `group_id`, fetch its name so the
  // active-filter chip is recognizable rather than a raw uuid.
  const { data: filterGroup } = useQuery({
    queryKey: ['groups', filterGroupId],
    queryFn: () => groupsApi.get(filterGroupId),
    enabled: !!filterGroupId,
  })

  // Used to resolve the group name on shared transaction rows.
  const { data: allGroups } = useQuery({
    queryKey: ['groups', 'all'],
    queryFn: () => groupsApi.list(true),
    staleTime: 60_000,
  })
  const groupNameById = useMemo(() => {
    const map = new Map<string, string>()
    for (const g of allGroups ?? []) map.set(g.id, g.name)
    return map
  }, [allGroups])

  const addTagFilter = (tag: string) => {
    const normalized = tag.startsWith('#') ? tag : `#${tag}`
    setTagFilters(prev => (prev.includes(normalized) ? prev : [...prev, normalized]))
    setPage(1)
  }
  const removeTagFilter = (tag: string) => {
    setTagFilters(prev => prev.filter(t => t !== tag))
    setPage(1)
  }
  const clearTagFilters = () => {
    setTagFilters([])
    setPage(1)
  }
  const [exporting, setExporting] = useState(false)
  const [transferDialogOpen, setTransferDialogOpen] = useState(false)
  const [linkTransferDialogOpen, setLinkTransferDialogOpen] = useState(false)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [lastSelectedId, setLastSelectedId] = useState<string | null>(null)
  const grid = useTransactionsGridState()
  const [bulkCategory, setBulkCategory] = useState<string>('')
  const [bulkAddToGroupOpen, setBulkAddToGroupOpen] = useState(false)
  const [bulkTagInput, setBulkTagInput] = useState<string>('')
  const [createRuleOpen, setCreateRuleOpen] = useState(false)
  const [createRuleInitialData, setCreateRuleInitialData] = useState<RuleDialogInitialData | undefined>(undefined)
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(null)
  const highlightId = searchParams.get('highlight')
  const highlightedRowRef = useRef<HTMLTableRowElement | null>(null)
  // Last URL query we synced from, to tell a genuine navigation apart from the
  // initial mount (and from StrictMode's double-invoke, which repeats the same
  // value). Starts null so the first run is recognized as the initial mount.
  const prevSearchRef = useRef<string | null>(null)

  // Sync state from URL when navigating (e.g. from the command palette) while
  // the page is already mounted. Typing in the search box does not touch the
  // URL, so this effect only fires on genuine navigation events.
  useEffect(() => {
    const search = searchParams.toString()
    // Skip re-runs with an unchanged query (e.g. StrictMode's second mount),
    // so they can't override the initial current-month default.
    if (prevSearchRef.current === search) return
    prevSearchRef.current = search

    const nextQ = searchParams.get('q') ?? ''
    setSearchInput(nextQ)
    setSearchQuery(nextQ)
    const tags = searchParams.get('tags');
    setTagFilters(tags ? tags.split(',') : []);
    setFilterPayee(searchParams.get('payee_id') ?? '')
    setFilterGroupId(searchParams.get('group_id') ?? '')
    setFilterType(searchParams.get('type') ?? '')
    const categories = searchParams.get('category_id');
    setFilterCategoryIds(categories ? categories.split(',') : []);
    setFilterUncategorized(searchParams.get('uncategorized') === '1');
    const accounts = searchParams.get('account_id');
    setFilterAccountIds(accounts ? accounts.split(',') : []);
    const urlFrom = searchParams.get('from')
    const urlTo = searchParams.get('to')
    if (urlFrom || urlTo) {
      // Explicit range in the URL (shared/bookmarked link) wins.
      setFilterFrom(urlFrom ?? '')
      setFilterTo(urlTo ?? '')
    } else {
      // No explicit range: fall back to the current month. The month is the
      // page's default view (and what "Limpar filtros" restores), so the
      // header stepper, the date chip and the listing never diverge.
      const { from, to } = monthRange(currentMonth())
      setFilterFrom(from)
      setFilterTo(to)
    }
    setFilterMinAmount(searchParams.get('min_amount') ?? '');
    setFilterMaxAmount(searchParams.get('max_amount') ?? '');
    setPage(1)
  }, [searchParams])

  // Keep the URL in sync with the current filters, so that the current page can be
  // refreshed, bookmarked or shared.
  useEffect(() => {
    const params = new URLSearchParams(
      [
        ['q', searchQuery],
        ['tags', tagFilters.join(',')],
        ['payee_id', filterPayee],
        ['group_id', filterGroupId],
        ['type', filterType],
        ['category_id', filterCategoryIds.join(',')],
        ['uncategorized', filterUncategorized ? '1' : ''],
        ['account_id', filterAccountIds.join(',')],
        ['from', filterFrom],
        ['to', filterTo],
        ['min_amount', filterMinAmount],
        ['max_amount', filterMaxAmount],
      ].filter(([, v]) => v.length),
    );

    window.history.replaceState(
      null,
      '',
      params.size ? `?${params}` : window.location.pathname,
    );
  }, [
    searchQuery,
    tagFilters,
    filterPayee,
    filterGroupId,
    filterType,
    filterCategoryIds,
    filterUncategorized,
    filterAccountIds,
    filterFrom,
    filterTo,
    filterMinAmount,
    filterMaxAmount,
  ]);

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      setSearchQuery(searchInput)
      setPage(1)
    }, 300)
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current) }
  }, [searchInput])

  // Clear selection on page/filter change
  useEffect(() => {
    setSelectedIds(new Set())
    setLastSelectedId(null)
    setBulkCategory('')
  }, [page, filterAccountIds, filterCategoryIds, filterUncategorized, filterPayee, filterType, filterFrom, filterTo, filterMinAmount, filterMaxAmount, searchQuery])

  // Reset bulk category when selection changes so the same category can be re-applied
  useEffect(() => {
    setBulkCategory('')
  }, [selectedIds])

  // Scroll to and flash a highlighted row after navigation (e.g. opened via
  // the command palette). Re-runs whenever highlightId or the current data
  // set changes so that when results finish loading we animate the row.
  useEffect(() => {
    if (!highlightId) return
    const el = highlightedRowRef.current
    if (!el) return
    const raf = requestAnimationFrame(() => {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' })
      el.classList.add('talisma-highlight-flash')
    })
    const timer = setTimeout(() => {
      el.classList.remove('talisma-highlight-flash')
    }, 2500)
    return () => {
      cancelAnimationFrame(raf)
      clearTimeout(timer)
      el.classList.remove('talisma-highlight-flash')
    }
  }, [highlightId, searchQuery, filterPayee, filterCategoryIds, page])

  // Merge the global active-collection filter with the page's own account
  // filter (issue #105): an explicit on-page account selection wins; otherwise
  // scope to the active collection's accounts. null collection = all accounts.
  const effectiveAccountIds = filterAccountIds.length > 0
    ? filterAccountIds
    : (activeAccountIds ?? [])
  // Wallet-only collection active (zero accounts) and no explicit on-page
  // account filter → there are no matching transactions; show empty rather
  // than falling back to all accounts.
  const noAccounts = filterAccountIds.length === 0
    && activeAccountIds !== null && activeAccountIds.length === 0

  const { data, isLoading } = useQuery({
    queryKey: ['transactions', page, limit, effectiveAccountIds, filterCategoryIds, filterUncategorized, filterPayee, filterGroupId, filterType, filterFrom, filterTo, filterMinAmount, filterMaxAmount, searchQuery, tagFilters, grid.sortBy, grid.sortDir],
    enabled: !noAccounts,
    queryFn: () =>
      transactions.list({
        page,
        limit,
        account_ids: effectiveAccountIds.length > 0 ? effectiveAccountIds : undefined,
        category_ids: filterCategoryIds.length > 0 ? filterCategoryIds : undefined,
        payee_id: filterPayee || undefined,
        group_id: filterGroupId || undefined,
        type: filterType || undefined,
        uncategorized: filterUncategorized ? true : undefined,
        from: filterFrom || undefined,
        to: filterTo || undefined,
        min_amount: filterMinAmount ? Number(filterMinAmount) : undefined,
        max_amount: filterMaxAmount ? Number(filterMaxAmount) : undefined,
        q: searchQuery || undefined,
        tags: tagFilters.length > 0 ? tagFilters : undefined,
        ...grid.apiSort,
      }),
  })

  // Publish the active filters + result count to the global chat panel.
  // The agent uses this so "what about THIS list?" / "soma essas" /
  // "categorize these" resolve against the filtered view, not the user's
  // entire history. Free-form blob — backend turns it into a primer.
  const ctxFilters = {
    search: searchQuery || undefined,
    account_ids: effectiveAccountIds.length ? effectiveAccountIds : undefined,
    category_ids: filterCategoryIds.length ? filterCategoryIds : undefined,
    payee_id: filterPayee || undefined,
    group_id: filterGroupId || undefined,
    type: filterType || undefined,
    uncategorized: filterUncategorized || undefined,
    from: filterFrom || undefined,
    to: filterTo || undefined,
    min_amount: filterMinAmount || undefined,
    max_amount: filterMaxAmount || undefined,
    tags: tagFilters.length ? tagFilters : undefined,
    sort_by: grid.sortBy,
    sort_dir: grid.sortDir,
    page,
    limit,
  }
  const ctxKey = JSON.stringify(ctxFilters) + ':' + (data?.total ?? '')
  useRegisterPageChatContext(
    {
      path: '/transactions',
      label: 'Transactions',
      summary: data?.total != null
        ? `${data.total} transaction(s) match the active filters (showing page ${page}, ${limit} per page).`
        : 'Transactions list with active filters.',
      filters: ctxFilters,
    },
    ctxKey,
  )

  const { data: categoriesList } = useQuery({
    queryKey: ['categories'],
    queryFn: categoriesApi.list,
  })

  const { data: categoryGroupsList } = useQuery({
    queryKey: ['categoryGroups'],
    queryFn: categoryGroupsApi.list,
  })

  const { data: accountsList } = useQuery({
    queryKey: ['accounts'],
    queryFn: () => accountsApi.list(),
  })

  const { data: payeesList } = useQuery({
    queryKey: ['payees'],
    queryFn: payeesApi.list,
  })

  const { data: recurringList } = useQuery({
    queryKey: ['recurring'],
    queryFn: recurring.list,
  })

  const { data: accountingModeData } = useQuery({
    queryKey: ['admin', 'accounting-mode'],
    queryFn: () => admin.accountingMode(),
    staleTime: 5 * 60 * 1000,
  })
  const isAccrual = accountingModeData?.mode === 'accrual'

  // Effective date shown in the listing: the manual bill-cycle override when
  // set, otherwise the computed effective date (accrual) or purchase date.
  const displayDate = (tx: Transaction) =>
    tx.effective_bill_date ?? (isAccrual ? tx.effective_date : tx.date)

  const invalidateAfterTxMutation = () => invalidateFinancialQueries(queryClient)

  const createMutation = useMutation({
    mutationFn: async (payload: { tx: Partial<Transaction>; recurringData?: { frequency: string; end_date?: string }; pendingFiles?: File[]; action?: SaveAction }) => {
      const created = await transactions.create(payload.tx)
      if (payload.recurringData) {
        await recurring.create({
          description: payload.tx.description,
          amount: payload.tx.amount,
          currency: payload.tx.currency ?? userCurrency,
          type: payload.tx.type,
          frequency: payload.recurringData.frequency,
          start_date: payload.tx.date,
          end_date: payload.recurringData.end_date || undefined,
          category_id: payload.tx.category_id || undefined,
          account_id: payload.tx.account_id || undefined,
          skip_first: true,
        } as Record<string, unknown>)
      }
      if (payload.pendingFiles?.length) {
        await Promise.all(
          payload.pendingFiles.map(file => transactions.attachments.upload(created.id, file))
        )
      }
      return created
    },
    onSuccess: (_created, variables) => {
      invalidateAfterTxMutation()
      queryClient.invalidateQueries({ queryKey: ['recurring'] })
      toast.success(t('transactions.created'))
      if (variables.action === 'saveAndNew') {
        setDuplicateDraft(null)
        setFormResetKey(k => k + 1)
      } else if (variables.action === 'saveAndDuplicate') {
        setDuplicateDraft(variables.tx)
        setFormResetKey(k => k + 1)
      } else {
        setDialogOpen(false)
      }
    },
    onError: (error) => {
      toast.error(extractApiError(error))
    },
  })

  const createInstallmentsMutation = useMutation({
    mutationFn: async (payload: {
      plan: {
        account_id: string
        description: string
        total_amount: number
        num_installments: number
        purchase_date: string
        category_id?: string | null
        payee_id?: string | null
        currency?: string
        notes?: string
        effective_bill_date?: string | null
      }
      pendingFiles?: File[]
    }) => {
      const created = await transactions.createInstallments(payload.plan)
      if (payload.pendingFiles?.length && created.length > 0) {
        await Promise.all(
          payload.pendingFiles.map(file => transactions.attachments.upload(created[0].id, file))
        )
      }
      return created
    },
    onSuccess: () => {
      invalidateAfterTxMutation()
      toast.success(t('transactions.created'))
      setDialogOpen(false)
    },
    onError: (error) => {
      toast.error(extractApiError(error))
    },
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, ...data }: TransactionUpdatePayload & { id: string }) =>
      transactions.update(id, data),
    onSuccess: () => {
      invalidateAfterTxMutation()
      setDialogOpen(false)
      setEditingTx(null)
      toast.success(t('transactions.updated'))
    },
    onError: (error) => {
      toast.error(extractApiError(error))
    },
  })

  const markPaidMutation = useMutation({
    mutationFn: ({ id }: { id: string }) =>
      transactions.update(id, { status: 'posted' }),
    onSuccess: () => {
      invalidateAfterTxMutation()
      toast.success(t('transactions.paid'))
    },
    onError: (error) => {
      toast.error(extractApiError(error))
    },
  })

  const revertToScheduledMutation = useMutation({
    mutationFn: ({ id }: { id: string }) =>
      transactions.update(id, { status: 'scheduled' }),
    onSuccess: () => {
      invalidateAfterTxMutation()
    },
    onError: (error) => {
      toast.error(extractApiError(error))
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => transactions.delete(id),
    onSuccess: () => {
      invalidateAfterTxMutation()
      setDialogOpen(false)
      setEditingTx(null)
      toast.success(t('transactions.deleted'))
    },
    onError: (error) => {
      toast.error(extractApiError(error))
    },
  })

  const bulkCategorizeMutation = useMutation({
    mutationFn: ({ ids, categoryId }: { ids: string[]; categoryId: string | null }) =>
      transactions.bulkCategorize(ids, categoryId),
    onSuccess: (result) => {
      invalidateAfterTxMutation()
      setSelectedIds(new Set())
      setBulkCategory('')
      toast.success(t('transactions.bulkSuccess', { count: result.updated }))
    },
    onError: (error) => {
      toast.error(extractApiError(error))
    },
  })

  const bulkAddTagsMutation = useMutation({
    mutationFn: ({ ids, tags }: { ids: string[]; tags: string[] }) =>
      transactions.bulkAddTags(ids, tags),
    onSuccess: (result) => {
      invalidateAfterTxMutation()
      setSelectedIds(new Set())
      setBulkTagInput('')
      toast.success(t('transactions.bulkSuccess', { count: result.updated }))
    },
    onError: (error) => {
      toast.error(extractApiError(error))
    },
  })

  const bulkRemoveTagsMutation = useMutation({
    mutationFn: ({ ids, tags }: { ids: string[]; tags: string[] }) =>
      transactions.bulkRemoveTags(ids, tags),
    onSuccess: (result) => {
      invalidateAfterTxMutation()
      setSelectedIds(new Set())
      setBulkTagInput('')
      toast.success(t('transactions.bulkSuccess', { count: result.updated }))
    },
    onError: (error) => {
      toast.error(extractApiError(error))
    },
  })

  const bulkAddToGroupMutation = useMutation({
    mutationFn: ({ ids, payload }: { ids: string[]; payload: BulkAddToGroupSubmission }) =>
      transactions.bulkAddToGroup(ids, payload.groupId, {
        share_type: payload.share_type,
        member_splits: payload.member_splits,
      }),
    onSuccess: (result) => {
      invalidateAfterTxMutation()
      setSelectedIds(new Set())
      setBulkAddToGroupOpen(false)
      if (result.skipped > 0) {
        toast.success(t('transactions.bulkAddToGroupPartial', { added: result.updated, skipped: result.skipped }))
      } else {
        toast.success(t('transactions.bulkAddToGroupSuccess', { count: result.updated }))
      }
    },
    onError: (error) => {
      toast.error(extractApiError(error))
    },
  })

  const linkTransferMutation = useMutation({
    mutationFn: (ids: [string, string]) => transactions.linkTransfer(ids),
    onSuccess: () => {
      invalidateAfterTxMutation()
      queryClient.invalidateQueries({ queryKey: ['transfer-candidates'] })
      setLinkTransferDialogOpen(false)
      setSelectedIds(new Set())
      toast.success(t('transactions.linkTransferSuccess'))
    },
    onError: (error) => {
      toast.error(extractApiError(error))
    },
  })

  const createCounterpartMutation = useMutation({
    mutationFn: ({ anchorId, toAccountId }: { anchorId: string; toAccountId: string }) =>
      transactions.createTransferCounterpart(anchorId, toAccountId),
    onSuccess: () => {
      invalidateAfterTxMutation()
      queryClient.invalidateQueries({ queryKey: ['transfer-candidates'] })
      setLinkTransferDialogOpen(false)
      setSelectedIds(new Set())
      toast.success(t('transactions.linkTransferSuccess'))
    },
    onError: (error) => {
      toast.error(extractApiError(error))
    },
  })

  const unlinkTransferMutation = useMutation({
    mutationFn: (pairId: string) => transactions.unlinkTransfer(pairId),
    onSuccess: () => {
      invalidateAfterTxMutation()
      setDialogOpen(false)
      setEditingTx(null)
      toast.success(t('transactions.unlinkTransferSuccess'))
    },
    onError: (error) => {
      toast.error(extractApiError(error))
    },
  })

  const transferMutation = useMutation({
    mutationFn: (data: {
      from_account_id: string
      to_account_id: string
      amount: number
      date: string
      description: string
      notes?: string
      fx_rate?: number
    }) => transactions.createTransfer(data),
    onSuccess: () => {
      invalidateAfterTxMutation()
      setTransferDialogOpen(false)
      toast.success(t('transactions.transferCreated'))
    },
    onError: (error) => {
      toast.error(extractApiError(error))
    },
  })

  const createRuleMutation = useMutation({
    mutationFn: (data: Omit<Rule, 'id' | 'user_id'>) => rulesApi.create(data),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['rules'] })
      setCreateRuleOpen(false)
      setCreateRuleInitialData(undefined)
      const applied = result.applied_count ?? 0
      if (applied > 0) {
        invalidateAfterTxMutation()
        toast.success(t('rules.createdAndApplied', { count: applied }))
      } else {
        toast.success(t('rules.created'))
      }
    },
    onError: (error: unknown) => {
      const err = error as { response?: { status?: number } }
      if (err?.response?.status === 409) {
        toast.error(t('rules.duplicateName'))
      } else {
        toast.error(t('common.error'))
      }
    },
  })

  const handleCreateRuleFromTransaction = (tx: Transaction) => {
    const conditions = [
      { field: 'description', op: 'contains', value: tx.description },
    ]
    if (tx.payee_id) {
      conditions.push({ field: 'payee_id', op: 'equals', value: tx.payee_id })
    }
    const actions: { op: string; value: string }[] = tx.category_id
      ? [{ op: 'set_category', value: tx.category_id }]
      : [{ op: 'set_category', value: '' }]
    const tags = parseHashtags(tx.notes)
    if (tags.length > 0) {
      actions.push({ op: 'append_notes', value: tags.join(' ') })
    }
    setCreateRuleInitialData({ conditions, actions })
    setCreateRuleOpen(true)
  }

  const toggleSelect = (id: string, isShiftKey: boolean = false) => {
    setSelectedIds(prev =>
      calculateRangeSelection(prev, lastSelectedId, id, filteredItems, isShiftKey, tx => !tx.is_shared)
    )
    setLastSelectedId(id)
  }

  // Tag filtering is now applied server-side, so the visible list and the
  // page count both reflect the same filtered total — issue #88.
  const filteredItems = data?.items ?? []

  // Transfer pairs collapse into a single "Origem ➔ Destino" row in the All
  // Accounts view. Only merged when both legs are present on the current page
  // (an account filter showing just one leg keeps the individual row).
  const pairRowsById = useMemo(() => {
    const map = new Map<string, TransferPairRow>()
    const items = data?.items ?? []
    for (const tx of items) {
      if (!tx.transfer_pair_id) continue
      if (map.has(tx.transfer_pair_id)) continue
      const partner = items.find(t => t.transfer_pair_id === tx.transfer_pair_id && t.id !== tx.id)
      if (!partner) continue
      const debit = tx.type === 'debit' ? tx : partner
      const credit = tx.type === 'credit' ? tx : partner
      map.set(tx.transfer_pair_id, {
        kind: 'transfer-pair',
        key: `pair-${tx.transfer_pair_id}`,
        pairId: tx.transfer_pair_id,
        legIds: [debit.id, credit.id],
        debit,
        credit,
        shared: !!(debit.is_shared || credit.is_shared),
      })
    }
    return map
  }, [data?.items])

  const displayRows = useMemo<DisplayRow[]>(() => {
    const items = data?.items ?? []
    if (pairRowsById.size === 0) return items
    const rows: DisplayRow[] = []
    const consumed = new Set<string>()
    for (const tx of items) {
      if (consumed.has(tx.id)) continue
      if (tx.transfer_pair_id) {
        const pair = pairRowsById.get(tx.transfer_pair_id)
        if (pair) {
          rows.push(pair)
          pair.legIds.forEach(id => consumed.add(id))
          continue
        }
      }
      rows.push(tx)
    }
    return rows
  }, [data?.items, pairRowsById])

  const rowIds = (row: DisplayRow): string[] =>
    row.kind === 'transfer-pair' ? row.legIds : [row.id]
  const rowShared = (row: DisplayRow): boolean =>
    row.kind === 'transfer-pair' ? row.shared : !!row.is_shared
  const rowSelected = (row: DisplayRow): boolean => rowIds(row).every(id => selectedIds.has(id))
  const rowSomeSelected = (row: DisplayRow): boolean => rowIds(row).some(id => selectedIds.has(id))

  const toggleRow = (row: DisplayRow) => {
    const ids = rowIds(row)
    setSelectedIds(prev => {
      const selected = ids.every(id => prev.has(id))
      const next = new Set(prev)
      if (selected) ids.forEach(id => next.delete(id))
      else ids.forEach(id => next.add(id))
      return next
    })
    setLastSelectedId(ids[ids.length - 1])
  }

  const selectableRows = displayRows.filter(row => !rowShared(row))

  const allSelected = selectableRows.length > 0 && selectableRows.every(row => rowSelected(row))
  const someSelected = selectableRows.some(row => rowSomeSelected(row)) && !allSelected

  const toggleSelectAll = () => {
    if (!selectableRows.length) return
    if (allSelected) {
      setSelectedIds(new Set())
    } else {
      setSelectedIds(new Set(selectableRows.flatMap(rowIds)))
    }
  }

  // Net total of the currently-selected rows (issue #185). Selection is
  // always page-scoped (cleared on page/filter change), so summing the
  // visible page covers every selected id. Cross-currency rows use their
  // primary-currency amount; credits add, debits subtract.
  const selectedNet = useMemo(() => {
    let net = 0
    for (const tx of data?.items ?? []) {
      if (!selectedIds.has(tx.id)) continue
      const base = Math.abs(Number(tx.amount_primary ?? tx.amount))
      net += tx.type === 'credit' ? base : -base
    }
    return net
  }, [data?.items, selectedIds])

  // Resolve the currently-selected transactions into a valid debit/credit pair
  // for the "Link as transfer" action. Returns null if the pair is invalid
  // (wrong count, same account, same type, or already linked).
  const linkablePair = useMemo(() => {
    if (selectedIds.size !== 2) return null
    const selected = (data?.items ?? []).filter(tx => selectedIds.has(tx.id))
    if (selected.length !== 2) return null
    if (selected.some(tx => tx.transfer_pair_id)) return null
    if (selected[0].account_id === selected[1].account_id) return null
    const debit = selected.find(tx => tx.type === 'debit')
    const credit = selected.find(tx => tx.type === 'credit')
    if (!debit || !credit) return null
    return { debit, credit }
  }, [selectedIds, data?.items])

  // Single-selection picker mode: when exactly one unlinked transaction is
  // selected, the user can search for its counterpart across all accounts.
  const linkAnchor = useMemo(() => {
    if (selectedIds.size !== 1) return null
    const selected = (data?.items ?? []).find(tx => selectedIds.has(tx.id))
    if (!selected) return null
    if (selected.transfer_pair_id) return null
    return selected
  }, [selectedIds, data?.items])

  const canOpenLinkDialog = !!linkablePair || !!linkAnchor
  const linkDisabledTooltip =
    !canOpenLinkDialog && selectedIds.size >= 2
      ? t('transactions.linkTransferInvalidPair')
      : undefined

  const totalPages = data ? Math.ceil(data.total / limit) : 0

  const handleTransactionSave = (
    data: Partial<Transaction>,
    recurringData?: { frequency: string; end_date?: string },
    pendingFiles?: File[],
    action?: SaveAction,
  ) => {
    if (!editingTx) {
      createMutation.mutate({ tx: data, recurringData, pendingFiles, action })
      return
    }

    // A transfer shares its custom category across both legs, so a category
    // change on one leg always cascades to its paired transaction.
    const payload: TransactionUpdatePayload =
      editingTx.transfer_pair_id &&
      Object.prototype.hasOwnProperty.call(data, 'category_id') &&
      data.category_id !== editingTx.category_id
        ? { ...data, apply_to_transfer_pair: true }
        : data

    updateMutation.mutate({ id: editingTx.id, ...payload })
  }

  const handleCreateInstallments = (
    plan: {
      account_id: string
      description: string
      total_amount: number
      num_installments: number
      purchase_date: string
      category_id?: string | null
      payee_id?: string | null
      currency?: string
      notes?: string
      effective_bill_date?: string | null
    },
    pendingFiles?: File[],
  ) => {
    createInstallmentsMutation.mutate({ plan, pendingFiles })
  }

  // Open the Add Transaction dialog seeded from an existing row's
  // fields (issue #158). Identity-bearing fields (id, transfer_pair,
  // installment series, splits) are dropped so the dialog treats the
  // result as a brand-new transaction; the user can tweak the date or
  // any other field before saving.
  const handleDuplicateTransaction = (tx: Transaction) => {
    const draft: Partial<Transaction> = {
      description: tx.description,
      amount: tx.amount,
      currency: tx.currency,
      type: tx.type,
      date: tx.date,
      account_id: tx.account_id,
      category_id: tx.category_id,
      payee_id: tx.payee_id,
      payee: tx.payee,
      payee_name: tx.payee_name,
      notes: tx.notes,
    }
    setEditingTx(null)
    setDuplicateDraft(draft)
    setFormResetKey(k => k + 1)
    setDialogOpen(true)
  }

  const handleExport = async () => {
    setExporting(true)
    try {
      if (selectedIds.size > 0) {
        // Selection-only export bypasses other filters and hits the
        // backend's `transaction_ids` short-circuit.
        await transactions.export({ transaction_ids: Array.from(selectedIds) })
      } else {
        await transactions.export({
          account_ids: effectiveAccountIds.length > 0 ? effectiveAccountIds : undefined,
          category_ids: filterCategoryIds.length > 0 ? filterCategoryIds : undefined,
          payee_id: filterPayee || undefined,
          type: filterType || undefined,
          uncategorized: filterUncategorized ? true : undefined,
          from: filterFrom || undefined,
          to: filterTo || undefined,
          q: searchQuery || undefined,
          tags: tagFilters.length > 0 ? tagFilters : undefined,
        })
      }
      toast.success(t('transactions.exportSuccess'))
    } catch {
      toast.error(t('transactions.exportError'))
    } finally {
      setExporting(false)
    }
  }

  // Resize: track which column is being dragged so we can clear listeners
  // when the gesture ends. The width is committed to grid state on every
  // pointermove for live feedback (cheap — single React state update).
  const resizingRef = useRef<{ id: ColumnId; startX: number; startWidth: number } | null>(null)
  const startResize = (e: React.PointerEvent<HTMLSpanElement>, col: ColumnDef) => {
    e.preventDefault()
    e.stopPropagation()
    resizingRef.current = { id: col.id, startX: e.clientX, startWidth: grid.widthOf(col.id) }
    const onMove = (ev: PointerEvent) => {
      const r = resizingRef.current
      if (!r) return
      grid.setWidth(r.id, r.startWidth + (ev.clientX - r.startX))
    }
    const onUp = () => {
      resizingRef.current = null
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
  }

  // Column widths: every column except Description renders at a fixed px
  // width; Description is the table's single flexible column and absorbs the
  // remaining space, capped at DESCRIPTION_MAX_WIDTH. table-layout: fixed
  // ignores min/max-width on cells, so the cap is applied by measuring the
  // container and setting an explicit width on the Description column; the
  // leftover space is then redistributed proportionally to the fixed columns
  // (so Description never grows unbounded while Category/Account/Date still
  // gain room on wide viewports). On a 1366x768 viewport (~1030px usable) the
  // fixed columns sum to ~671px, so Description gets ~320px and Category
  // ~160px. The table stays w-full/table-fixed at 100% of the container (no
  // growing min-width floor), and every cell clips its own overflow, so
  // nothing ever forces horizontal scroll or paints over a neighbour column.
  const checkboxColWidth = 36
  const tableRef = useRef<HTMLDivElement | null>(null)
  const [tableWidth, setTableWidth] = useState(0)
  useLayoutEffect(() => {
    const el = tableRef.current
    if (!el) return
    const update = () => setTableWidth(el.clientWidth)
    update()
    const ro = new ResizeObserver(update)
    ro.observe(el)
    return () => ro.disconnect()
  }, [isLoading])

  const fixedCols = grid.visibleColumns.filter(c => c.id !== FLEXIBLE_COL_ID)
  const fixedSum = fixedCols.reduce((s, c) => s + grid.widthOf(c.id), 0) + checkboxColWidth
  const descWidth = Math.max(
    DESCRIPTION_MIN_WIDTH,
    Math.min(DESCRIPTION_MAX_WIDTH, tableWidth > 0 ? tableWidth - fixedSum : DESCRIPTION_MAX_WIDTH),
  )
  const leftover = Math.max(0, tableWidth - fixedSum - descWidth)
  const fixedBase = fixedSum - checkboxColWidth
  const growFactor = fixedBase > 0 && leftover > 0 ? (fixedBase + leftover) / fixedBase : 1

  const columnWidthStyle = (col: ColumnDef): React.CSSProperties => {
    if (col.id === FLEXIBLE_COL_ID) {
      return { width: descWidth, minWidth: descWidth }
    }
    const width = Math.round(grid.widthOf(col.id) * growFactor)
    return { width, minWidth: width }
  }

  const renderHeaderCell = (col: ColumnDef) => {
    const isSorted = grid.sortBy === col.id
    const sortIndicator = isSorted ? (grid.sortDir === 'asc' ? <ArrowUp size={12} /> : <ArrowDown size={12} />) : null
    const alignClass = col.align === 'right' ? 'text-right' : 'text-left'
    const justify = col.align === 'right' ? 'justify-end' : 'justify-start'
    const cursorClass = col.sortable ? 'cursor-pointer select-none hover:text-foreground' : ''
    // Match the amount/attachments body cells' pr-2 so right-aligned
    // headers line up with their values (issue #161 polish).
    const padX = col.align === 'right' ? 'pr-2' : ''
    return (
      <TableHead
        key={col.id}
        style={columnWidthStyle(col)}
        className={`relative text-xs font-medium text-muted-foreground py-3 ${alignClass} ${padX}`}
        onClick={() => { if (col.sortable) grid.toggleSort(col.id) }}
      >
        <div className={`flex items-center gap-1 ${justify} ${cursorClass}`}>
          <span className="truncate">{t(col.labelKey)}</span>
          {sortIndicator}
        </div>
        {col.id !== FLEXIBLE_COL_ID && (
          <span
            onPointerDown={(e) => startResize(e, col)}
            onClick={(e) => e.stopPropagation()}
            aria-hidden="true"
            className="absolute right-0 top-0 h-full w-2 -mr-1 cursor-col-resize select-none hover:bg-primary/40 active:bg-primary/60"
          />
        )}
      </TableHead>
    )
  }

  const stripHashtags = (notes: string) => notes.replace(/#[\wÀ-ž-]+/g, '').trim()

  const renderAmountCell = (row: DisplayRow) => {
    if (row.kind === 'transfer-pair') {
      const tx = row.debit
      return (
        <>
          <span className="text-xs md:text-sm font-bold tabular-nums text-muted-foreground">
            {mask(formatCurrency(Math.abs(Number(tx.amount)), tx.currency, locale))}
          </span>
          {tx.amount_primary != null && tx.currency !== userCurrency && (
            <div className="flex items-center justify-end gap-1">
              <span className="text-[10px] text-muted-foreground tabular-nums">
                {mask(formatCurrency(Math.abs(tx.amount_primary), userCurrency, locale))}
              </span>
            </div>
          )}
        </>
      )
    }
    const tx = row
    const displayAmount = tx.is_shared && tx.viewer_share != null
      ? Number(tx.viewer_share)
      : Number(tx.amount)
    return (
      <>
        <span
          className={`text-xs md:text-sm font-bold tabular-nums ${
            tx.is_ignored ? 'text-gray-500': tx.type === 'credit' ? 'text-emerald-600' : 'text-rose-500'
          }`}
        >
          {mask(
            `${tx.is_ignored ? ' ' : tx.type === 'credit' ? '+' : '−'}${formatCurrency(
              Math.abs(displayAmount),
              tx.currency,
              locale,
            )}`,
          )}
        </span>
        {tx.is_shared && (
          <p className="text-[10px] text-muted-foreground tabular-nums">
            {t('splitGroups.sharedRowParent', {
              total: formatCurrency(Math.abs(Number(tx.amount)), tx.currency, locale),
            })}
          </p>
        )}
        {!tx.is_shared && tx.viewer_share != null
          && Math.abs(Number(tx.viewer_share)) !== Math.abs(Number(tx.amount)) && (
          <p className="text-[10px] text-muted-foreground tabular-nums">
            {t('splitGroups.ownerRowYourShare', {
              share: formatCurrency(Math.abs(Number(tx.viewer_share)), tx.currency, locale),
            })}
          </p>
        )}
        {tx.amount_primary != null && tx.currency !== userCurrency && (
          <div className="flex items-center justify-end gap-1">
            {tx.fx_fallback && (
              <span title={t('transactions.fxFallbackTooltip')}><AlertTriangle size={11} className="text-amber-500 shrink-0" /></span>
            )}
            <span className="text-[10px] text-muted-foreground tabular-nums">
              {mask(formatCurrency(Math.abs(tx.amount_primary), userCurrency, locale))}
            </span>
          </div>
        )}
      </>
    )
  }

  const renderDescriptionCell = (tx: Transaction) => {
    const showInlineDate = !grid.isVisible('date')
    const showInlineNotes = !grid.isVisible('notes')
    const showInlineTags = !grid.isVisible('tags')
    const noteText = tx.notes ? stripHashtags(tx.notes) : ''
    const noteTags = tx.notes ? parseHashtags(tx.notes) : []
    return (
      <div className="flex items-center gap-2 md:gap-3 min-w-0">
        <CategoryIcon icon={tx.category?.icon} color={tx.category?.color} size="lg" />
        <div className="min-w-0 flex-1 overflow-hidden">
          <div className="flex items-center gap-1.5">
            <p className="text-sm font-semibold text-foreground flex-1 min-w-0 truncate">{tx.description}</p>
            <span className="flex items-center gap-1.5 shrink-0">
              {!!tx.transfer_pair_id && (
                <span title={t('transactions.transferTooltip')}>
                  <ArrowLeftRight size={14} className="shrink-0 text-blue-600" />
                </span>
              )}
              {tx.is_ignored && (
                <span title={t('transactions.ignored')}>
                  <EyeClosed size={14} className="shrink-0 text-muted-foreground" />
                </span>
              )}
              {(tx.recurring_transaction_id != null ||
                recurringList?.some(r => r.description === tx.description && r.type === tx.type)) && (
                <span
                  title={
                    tx.recurring_transaction_id != null
                      ? t('transactions.recurringLinkedTooltip')
                      : t('transactions.recurringBadge')
                  }
                >
                  <Repeat size={14} className="shrink-0 text-primary" />
                </span>
              )}
              {tx.group_id && (
                <span
                  title={
                    tx.is_shared && tx.parent_owner_name
                      ? t('splitGroups.sharedRowBadgeAuthor', {
                          author: tx.parent_owner_name,
                          group: groupNameById.get(tx.group_id) ?? '',
                        })
                      : t('splitGroups.ownerRowBadge', {
                          group: groupNameById.get(tx.group_id) ?? '',
                        })
                  }
                >
                  <Users size={14} className="shrink-0 text-violet-600" />
                </span>
              )}
              {tx.installment_number != null && tx.total_installments != null && (
                <span
                  className="inline-flex items-center text-[10px] font-bold tabular-nums text-amber-700 dark:text-amber-400 bg-amber-100 dark:bg-amber-500/20 border border-amber-200 dark:border-amber-500/30 px-1.5 py-0.5 rounded-full shrink-0"
                  title={tx.installment_total_amount != null
                    ? t('transactions.installmentTooltip', { count: tx.total_installments, total: tx.installment_total_amount })
                    : undefined}
                >
                  {tx.installment_number}/{tx.total_installments}
                </span>
              )}
              {(tx.attachment_count ?? 0) > 0 && (
                <Paperclip size={12} className="text-muted-foreground shrink-0" />
              )}
            </span>
          </div>
          {showInlineDate && (
            <p className="text-xs text-muted-foreground mt-0.5">
              {new Date(displayDate(tx) + 'T00:00:00').toLocaleDateString(dateLocale)}
              {displayDate(tx) !== tx.date && (
                <span className="text-[11px] text-muted-foreground/70">
                  {' · '}
                  {t('transactions.purchasedOn', { date: new Date(tx.date + 'T00:00:00').toLocaleDateString(dateLocale) })}
                </span>
              )}
            </p>
          )}
          {(showInlineNotes || showInlineTags) && tx.notes && (
            <div className="mt-1 space-y-0.5">
              {showInlineNotes && noteText && (
                <p className="text-xs text-muted-foreground italic leading-snug line-clamp-2">{noteText}</p>
              )}
              {showInlineTags && noteTags.length > 0 && (
                <div className="flex flex-wrap gap-1">
                  {noteTags.map((tag) => (
                    <span
                      key={tag}
                      className="inline-block text-[11px] font-medium bg-primary/5 text-primary border border-primary/10 px-1.5 py-0 rounded-full leading-5 cursor-pointer hover:bg-primary/10 transition-colors"
                      onClick={(e) => { e.stopPropagation(); addTagFilter(tag) }}
                    >
                      {tag}
                    </span>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    )
  }

  const renderStatusCell = (tx: Transaction) => {
    const editable = canWrite && !tx.is_shared
    return (
      <div className="flex items-center gap-1">
        {tx.status === 'scheduled' ? (
          editable ? (
            <Button
              variant="outline"
              size="sm"
              className="h-6 px-2 text-xs"
              onClick={(e) => {
                e.stopPropagation()
                markPaidMutation.mutate({ id: tx.id })
              }}
            >
              <Check size={12} className="mr-1" />
              {t('transactions.markPaid')}
            </Button>
          ) : (
            <span className="text-muted-foreground">{t('transactions.statusScheduled')}</span>
          )
        ) : (
          <span className="text-muted-foreground capitalize">
            {tx.status === 'pending'
              ? t('transactions.statusPending')
              : t('transactions.statusPosted')}
          </span>
        )}
        {editable && tx.status === 'posted' && (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button
                type="button"
                aria-label={t('transactions.undo')}
                className="h-6 w-6 flex items-center justify-center rounded-md border border-border bg-card text-muted-foreground hover:bg-muted/50 hover:text-foreground transition-colors cursor-pointer"
              >
                <ChevronDown size={12} />
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onClick={(e) => { e.stopPropagation(); revertToScheduledMutation.mutate({ id: tx.id }) }}>
                <RotateCcw size={12} className="mr-2" />
                {t('transactions.undo')}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      </div>
    )
  }

  const renderPairStatusCell = (pair: TransferPairRow) => {
    const statuses = new Set([pair.debit.status, pair.credit.status])
    const combined = statuses.size === 1
      ? pair.debit.status
      : statuses.has('scheduled')
        ? 'scheduled'
        : 'pending'
    const editable = canWrite && !pair.shared
    return (
      <div className="flex items-center gap-1">
        {combined === 'scheduled' ? (
          editable ? (
            <Button
              variant="outline"
              size="sm"
              className="h-6 px-2 text-xs"
              onClick={(e) => {
                e.stopPropagation()
                pair.legIds.forEach((id) => markPaidMutation.mutate({ id }))
              }}
            >
              <Check size={12} className="mr-1" />
              {t('transactions.markPaid')}
            </Button>
          ) : (
            <span className="text-muted-foreground">{t('transactions.statusScheduled')}</span>
          )
        ) : (
          <span className="text-muted-foreground capitalize">
            {combined === 'pending'
              ? t('transactions.statusPending')
              : t('transactions.statusPosted')}
          </span>
        )}
        {editable && combined === 'posted' && (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button
                type="button"
                aria-label={t('transactions.undo')}
                className="h-6 w-6 flex items-center justify-center rounded-md border border-border bg-card text-muted-foreground hover:bg-muted/50 hover:text-foreground transition-colors cursor-pointer"
              >
                <ChevronDown size={12} />
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onClick={(e) => { e.stopPropagation(); pair.legIds.forEach((id) => revertToScheduledMutation.mutate({ id })) }}>
                <RotateCcw size={12} className="mr-2" />
                {t('transactions.undo')}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      </div>
    )
  }

  const renderBodyCell = (col: ColumnDef, row: DisplayRow) => {
    const isPair = row.kind === 'transfer-pair'
    const tx: Transaction = isPair ? row.debit : row
    const widthStyle = columnWidthStyle(col)
    const alignClass = col.align === 'right' ? 'text-right' : ''
    const baseClass = `py-2.5 ${alignClass}`
    switch (col.id) {
      case 'date':
        return (
          <TableCell key={col.id} style={widthStyle} className={`${baseClass} min-w-0 overflow-hidden text-sm text-muted-foreground tabular-nums`}>
            <span className="block">
              {new Date(displayDate(tx) + 'T00:00:00').toLocaleDateString(dateLocale)}
            </span>
            {displayDate(tx) !== tx.date && (
              <span
                className="block text-[11px] text-muted-foreground/70 truncate"
                title={t('transactions.purchasedOn', { date: new Date(tx.date + 'T00:00:00').toLocaleDateString(dateLocale) })}
              >
                {t('transactions.purchasedOn', { date: new Date(tx.date + 'T00:00:00').toLocaleDateString(dateLocale) })}
              </span>
            )}
          </TableCell>
        )
      case 'description':
        return (
          <TableCell key={col.id} style={widthStyle} className={`${baseClass} pl-2 min-w-0 overflow-hidden whitespace-normal`}>
            {renderDescriptionCell(tx)}
          </TableCell>
        )
      case 'category':
        return (
          <TableCell key={col.id} style={widthStyle} className={`${baseClass} min-w-0 overflow-hidden text-left`}>
            {tx.category ? (
              <span className="inline-flex items-center justify-start gap-1.5 text-sm text-muted-foreground min-w-0">
                <CategoryIcon icon={tx.category.icon} color={tx.category.color} size="sm" />
                <span className="truncate min-w-0">{tx.category.name}</span>
              </span>
            ) : (
              <span className="text-xs text-muted-foreground italic">{t('transactions.noCategory')}</span>
            )}
          </TableCell>
        )
      case 'account': {
        if (isPair) {
          const fromAcc = accountsList?.find((a) => a.id === row.debit.account_id)
          const toAcc = accountsList?.find((a) => a.id === row.credit.account_id)
          return (
            <TableCell key={col.id} style={widthStyle} className={`${baseClass} min-w-0 overflow-hidden text-sm text-muted-foreground`}>
              <span className="inline-flex items-center gap-1 min-w-0" title={t('transactions.transferTooltip')}>
                <span className="truncate min-w-0" title={fromAcc ? getAccountName(fromAcc) : undefined}>{fromAcc ? getAccountName(fromAcc) : '—'}</span>
                <ArrowRight size={12} className="shrink-0 text-muted-foreground" />
                <span className="truncate min-w-0" title={toAcc ? getAccountName(toAcc) : undefined}>{toAcc ? getAccountName(toAcc) : '—'}</span>
              </span>
            </TableCell>
          )
        }
        const acc = accountsList?.find((a) => a.id === tx.account_id)
        return (
          <TableCell key={col.id} style={widthStyle} className={`${baseClass} min-w-0 overflow-hidden text-sm text-muted-foreground`}>
            {acc ? (
              <span className="flex items-center gap-2 min-w-0">
                <AccountIcon account={acc} size="sm" />
                <span className="truncate min-w-0" title={getAccountName(acc)}>{getAccountName(acc)}</span>
              </span>
            ) : (
              <span className="text-muted-foreground">—</span>
            )}
          </TableCell>
        )
      }
      case 'amount':
        return (
          <TableCell key={col.id} style={widthStyle} className={`${baseClass} pr-2 min-w-0 overflow-hidden`}>
            {renderAmountCell(row)}
          </TableCell>
        )
      case 'payee':
        return (
          <TableCell key={col.id} style={widthStyle} className={`${baseClass} min-w-0 overflow-hidden text-sm text-muted-foreground`}>
            {tx.payee_name ?? tx.payee ?? <span className="text-muted-foreground">—</span>}
          </TableCell>
        )
      case 'notes': {
        const text = tx.notes ? stripHashtags(tx.notes) : ''
        return (
          <TableCell key={col.id} style={widthStyle} className={`${baseClass} min-w-0 overflow-hidden text-xs text-muted-foreground italic whitespace-normal line-clamp-2`}>
            {text || <span className="not-italic">—</span>}
          </TableCell>
        )
      }
      case 'tags': {
        const tags = tx.notes ? parseHashtags(tx.notes) : []
        return (
          <TableCell key={col.id} style={widthStyle} className={`${baseClass} min-w-0 overflow-hidden`}>
            {tags.length === 0 ? (
              <span className="text-muted-foreground">—</span>
            ) : (
              <div className="flex flex-wrap gap-1">
                {tags.map((tag) => (
                  <span
                    key={tag}
                    className="inline-block text-[11px] font-medium bg-primary/5 text-primary border border-primary/10 px-1.5 py-0 rounded-full leading-5 cursor-pointer hover:bg-primary/10"
                    onClick={(e) => { e.stopPropagation(); addTagFilter(tag) }}
                  >
                    {tag}
                  </span>
                ))}
              </div>
            )}
          </TableCell>
        )
      }
      case 'attachments':
        return (
          <TableCell key={col.id} style={widthStyle} className={`${baseClass} pr-2 min-w-0 overflow-hidden text-sm text-muted-foreground tabular-nums`}>
            {(tx.attachment_count ?? 0) > 0 ? (
              <span className="inline-flex items-center gap-1 justify-end w-full">
                <Paperclip size={12} />{tx.attachment_count}
              </span>
            ) : <span>—</span>}
          </TableCell>
        )
      case 'type':
        if (isPair) {
          return (
            <TableCell key={col.id} style={widthStyle} className={`${baseClass} min-w-0 overflow-hidden text-sm`}>
              <span className="text-muted-foreground">{t('transactions.transfer')}</span>
            </TableCell>
          )
        }
        return (
          <TableCell key={col.id} style={widthStyle} className={`${baseClass} min-w-0 overflow-hidden text-sm`}>
            <span className={tx.type === 'credit' ? 'text-emerald-600' : 'text-rose-500'}>
              {tx.type === 'credit' ? t('transactions.typeIncome') : t('transactions.typeExpense')}
            </span>
          </TableCell>
        )
      case 'status':
        return (
          <TableCell key={col.id} style={widthStyle} className={`${baseClass} min-w-0 overflow-hidden text-sm`}>
            {isPair ? renderPairStatusCell(row) : renderStatusCell(tx)}
          </TableCell>
        )
    }
  }

  // A single non-shared, non-transfer row selected can be duplicated; shared
  // and transfer rows can't (issue #158). Computed once for both the desktop
  // button and the mobile overflow menu.
  const selectedSingleTx = canWrite && selectedIds.size === 1
    ? filteredItems.find(tx => selectedIds.has(tx.id))
    : undefined
  const duplicableTx = selectedSingleTx && !selectedSingleTx.is_shared && !selectedSingleTx.transfer_pair_id
    ? selectedSingleTx
    : null
  const exportLabel = exporting
    ? t('transactions.exporting')
    : selectedIds.size > 0
      ? t('transactions.exportSelected', { count: selectedIds.size })
      : t('transactions.exportCsv')

  return (
    <div>
      <PageHeader
        section={t('transactions.section')}
        title={t('transactions.title')}
        action={
          // Single row at every width: [month stepper] [+ Add Transaction] [⋯].
          // The stepper is compact and shrinks first so all three fit on a
          // phone (#257). On desktop the secondary actions (Columns, Export,
          // Duplicate, Transfer) are inline labelled buttons; on mobile they
          // collapse into the overflow menu so the row stays uncrowded.
          <div className="flex items-center gap-2 flex-wrap justify-end">
            <MonthStepper
              value={steppedMonth}
              onChange={handleMonthChange}
              locale={i18n.resolvedLanguage ?? i18n.language}
              prevLabel={t('transactions.monthPrevious')}
              nextLabel={t('transactions.monthNext')}
            />

            {/* Secondary actions: inline labelled buttons on desktop. */}
            <div className="hidden sm:contents">
              <TransactionsColumnPicker state={grid} />
              <Button variant="outline" disabled={exporting} onClick={handleExport}>
                <Download size={16} className="mr-1.5" />
                {exportLabel}
              </Button>
              {/* Duplicate (issue #158): single non-shared, non-transfer row
                  selected. Pre-fills Add Transaction from its fields. */}
              {duplicableTx && (
                <Button variant="outline" onClick={() => handleDuplicateTransaction(duplicableTx)}>
                  <Copy size={16} className="mr-1.5" />
                  {t('transactions.duplicate')}
                </Button>
              )}
              {canWrite && (
                <Button variant="outline" onClick={() => setTransferDialogOpen(true)}>
                  <ArrowLeftRight size={16} className="mr-1.5" />
                  {t('transactions.transfer')}
                </Button>
              )}
            </div>

            {/* Primary action: present at every width. */}
            {canWrite && (
              <Button onClick={() => { setEditingTx(null); setDialogOpen(true) }}>
                + {t('transactions.addManual')}
              </Button>
            )}

            {/* Secondary actions: overflow menu on mobile only. */}
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  variant="outline"
                  size="icon"
                  className="!size-9 sm:hidden"
                  aria-label={t('common.more')}
                >
                  <MoreHorizontal size={18} />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem disabled={exporting} onClick={handleExport}>
                  <Download size={16} className="mr-2" />
                  {exportLabel}
                </DropdownMenuItem>
                {duplicableTx && (
                  <DropdownMenuItem onClick={() => handleDuplicateTransaction(duplicableTx)}>
                    <Copy size={16} className="mr-2" />
                    {t('transactions.duplicate')}
                  </DropdownMenuItem>
                )}
                {canWrite && (
                  <DropdownMenuItem onClick={() => setTransferDialogOpen(true)}>
                    <ArrowLeftRight size={16} className="mr-2" />
                    {t('transactions.transfer')}
                  </DropdownMenuItem>
                )}
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        }
      />

      {/* Filters */}
      <TransactionsFilterBar
        searchInput={searchInput}
        onSearchChange={(v) => setSearchInput(v)}
        onSearchSubmit={(value) => {
          const trimmed = value.trim()
          // Tokenize submitted text. `#`-tokens become live tag filter
          // chips below the search bar (filtering applies immediately, no
          // Enter required). Non-`#` text remains as the free-text search
          // query (issue #88).
          const tokens = trimmed.split(/\s+/).filter(Boolean)
          const tags = tokens.filter(t => t.startsWith('#'))
          const text = tokens.filter(t => !t.startsWith('#')).join(' ')
          tags.forEach(addTagFilter)
          setSearchInput(text)
          setSearchQuery(text)
        }}
        filterAccountIds={filterAccountIds}
        onAccountIdsChange={(v) => { setFilterAccountIds(v); setPage(1) }}
        filterCategoryIds={filterCategoryIds}
        onCategoryIdsChange={(v) => { setFilterCategoryIds(v); setPage(1) }}
        filterUncategorized={filterUncategorized}
        onUncategorizedChange={(v) => { setFilterUncategorized(v); setPage(1) }}
        filterPayee={filterPayee}
        onPayeeChange={(v) => { setFilterPayee(v); setPage(1) }}
        filterGroupId={filterGroupId}
        onGroupIdChange={(v) => { setFilterGroupId(v); setPage(1) }}
        filterType={filterType}
        onTypeChange={(v) => { setFilterType(v); setPage(1) }}
        filterFrom={filterFrom}
        filterTo={filterTo}
        onDateRangeChange={(from, to) => {
          if (from || to) {
            setFilterFrom(from)
            setFilterTo(to)
          } else {
            // Clearing the date range restores the current month — the page's
            // default view — so the stepper and the listing stay consistent.
            const { from: f, to: t } = monthRange(currentMonth())
            setFilterFrom(f)
            setFilterTo(t)
          }
          setPage(1)
        }}
        filterMinAmount={filterMinAmount}
        filterMaxAmount={filterMaxAmount}
        onAmountRangeChange={(min, max) => { setFilterMinAmount(min); setFilterMaxAmount(max); setPage(1) }}
        onClearAll={() => {
          const { from, to } = monthRange(currentMonth())
          setFilterFrom(from)
          setFilterTo(to)
          setFilterAccountIds([])
          setFilterCategoryIds([])
          setFilterUncategorized(false)
          setFilterPayee('')
          setFilterGroupId('')
          setFilterType('')
          setFilterMinAmount('')
          setFilterMaxAmount('')
          setSearchInput('')
          setSearchQuery('')
          clearTagFilters()
          setPage(1)
        }}
        accounts={accountsList ?? []}
        categories={categoriesList ?? []}
        categoryGroups={categoryGroupsList ?? []}
        payees={payeesList ?? []}
        groups={allGroups ?? []}
      />
      {filterGroupId && (
        <div className="mb-4 flex flex-wrap items-center gap-1.5">
          <span className="inline-flex items-center gap-1.5 rounded-full border border-primary/15 bg-primary/5 px-3 py-1 text-xs font-medium text-primary">
            {t('splitGroups.title')}: {filterGroup?.name ?? '…'}
            <button
              onClick={() => { setFilterGroupId(''); setPage(1) }}
              className="ml-0.5 text-primary/60 hover:text-primary"
              aria-label="Clear group filter"
            >
              ×
            </button>
          </span>
        </div>
      )}
      {tagFilters.length > 0 && (
        <div className="mb-4 flex flex-wrap items-center gap-1.5">
          {tagFilters.map(tag => (
            <span
              key={tag}
              className="inline-flex items-center gap-1.5 rounded-full border border-primary/15 bg-primary/5 px-3 py-1 text-xs font-medium text-primary"
            >
              <span>{tag}</span>
              <button
                onClick={() => removeTagFilter(tag)}
                className="ml-0.5 text-primary/60 hover:text-primary"
                aria-label={`Remove ${tag} filter`}
              >
                <X size={12} />
              </button>
            </span>
          ))}
        </div>
      )}
      {isAccrual && (filterFrom || filterTo) && (
        <div className="mb-4 flex items-start gap-2 rounded-lg border border-border bg-muted/40 px-3 py-2 text-[11px] text-muted-foreground">
          <Info size={12} className="mt-0.5 shrink-0" />
          <span>{t('dashboard.accrualNote')}</span>
        </div>
      )}

      {/* Table */}
      <div className="bg-card rounded-xl border border-border shadow-sm overflow-hidden mb-4">
        {isLoading ? (
          <div className="p-6 space-y-3">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-14 w-full" />
            ))}
          </div>
        ) : (
          <div className="w-full max-w-full" ref={tableRef}>
          <Table style={{ tableLayout: 'fixed', minWidth: 0 }}>
            <TableHeader>
              <TableRow className="border-b border-border hover:bg-transparent">
                <TableHead style={{ width: checkboxColWidth, minWidth: checkboxColWidth }} className="py-3 pl-2 pr-0">
                  {canWrite && (
                    <input
                      type="checkbox"
                      checked={allSelected}
                      ref={(el) => { if (el) el.indeterminate = someSelected }}
                      onChange={toggleSelectAll}
                      className="h-4 w-4 rounded border-border accent-primary cursor-pointer"
                    />
                  )}
                </TableHead>
                {grid.visibleColumns.map(renderHeaderCell)}
              </TableRow>
            </TableHeader>
            <TableBody>
              {displayRows.map((row) => {
                const tx: Transaction = row.kind === 'transfer-pair' ? row.debit : row
                const shared = rowShared(row)
                const rowIsSelected = rowSelected(row)
                return (
                  <TableRow
                    key={row.kind === 'transfer-pair' ? row.key : row.id}
                    ref={tx.id === highlightId ? highlightedRowRef : undefined}
                    className={`hover:bg-muted border-b border-border last:border-0 ${
                      rowIsSelected ? 'bg-primary/5' : ''
                    } ${shared || !canWrite ? 'cursor-default' : 'cursor-pointer'}`}
                    onClick={() => {
                      if (shared) {
                        // Owned by another user — view in the group context instead.
                        if (tx.group_id) navigate(`/groups/${tx.group_id}`)
                        return
                      }
                      if (!canWrite) return
                      setEditingTx(tx)
                      setDialogOpen(true)
                    }}
                  >
                    <TableCell style={{ width: checkboxColWidth, minWidth: checkboxColWidth }} className="py-2.5 pl-2 pr-0">
                      {/* Bulk operations are scoped to user.id so they
                          silently skip shared rows — hide the checkbox
                          on those to avoid the dead-end UX. */}
                      {canWrite && !shared && (
                        <input
                          type="checkbox"
                          checked={rowIsSelected}
                          ref={(el) => {
                            if (el && row.kind === 'transfer-pair') {
                              el.indeterminate = rowSomeSelected(row) && !rowIsSelected
                            }
                          }}
                          onChange={() => {}}
                          onClick={(e) => {
                            e.stopPropagation()
                            if (row.kind === 'transfer-pair') toggleRow(row)
                            else toggleSelect(tx.id, e.shiftKey)
                          }}
                          className="h-4 w-4 rounded border-border accent-primary cursor-pointer"
                        />
                      )}
                    </TableCell>
                    {grid.visibleColumns.map(col => renderBodyCell(col, row))}
                  </TableRow>
                )
              })}
              {displayRows.length === 0 && (
                <TableRow>
                  <TableCell colSpan={grid.visibleColumns.length + 1} className="text-center py-16 text-muted-foreground">
                    {t('transactions.noResults')}
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
          </div>
        )}
        {/* Filtered summary (issue #185): income / expenses / net across
            ALL rows matching the active filters — not just this page. */}
        {!isLoading && data?.summary && displayRows.length > 0 && (
          <div className="flex flex-wrap items-center gap-x-5 gap-y-1 border-t border-border bg-muted/30 px-4 py-2.5">
            <span className="mr-auto text-xs text-muted-foreground">
              {t('transactions.summaryCount', { count: data.total })}
            </span>
            {/* Excluded (#242): transfers / investments / ignored, kept out of
                income/expense. Hidden when nothing was excluded in range. */}
            {data.summary.excluded > 0 && (
              <span className="flex items-baseline gap-1.5 text-xs">
                <span className="text-muted-foreground">{t('transactions.summaryExcluded')}</span>
                <span className="text-sm font-semibold tabular-nums text-muted-foreground">
                  {mask(formatCurrency(data.summary.excluded, data.summary.currency, locale))}
                </span>
              </span>
            )}
            <span className="flex items-baseline gap-1.5 text-xs">
              <span className="text-muted-foreground">{t('transactions.summaryIncome')}</span>
              <span className="text-sm font-semibold tabular-nums text-emerald-600">
                {mask(formatCurrency(data.summary.income, data.summary.currency, locale))}
              </span>
            </span>
            <span className="flex items-baseline gap-1.5 text-xs">
              <span className="text-muted-foreground">{t('transactions.summaryExpenses')}</span>
              <span className="text-sm font-semibold tabular-nums text-rose-500">
                {mask(formatCurrency(data.summary.expense, data.summary.currency, locale))}
              </span>
            </span>
            <span className="flex items-baseline gap-1.5 text-xs">
              <span className="text-muted-foreground">{t('transactions.summaryNet')}</span>
              <span
                className={`text-sm font-bold tabular-nums ${
                  data.summary.net >= 0 ? 'text-emerald-600' : 'text-rose-500'
                }`}
              >
                {mask(formatCurrency(data.summary.net, data.summary.currency, locale))}
              </span>
            </span>
          </div>
        )}
      </div>

      {/* Pagination */}
      {data && data.total > 10 && (
        <div className={`flex flex-col sm:flex-row items-center justify-between gap-4 py-4 ${selectedIds.size > 0 ? 'pb-20' : ''}`}>
          <div className="hidden sm:block w-32" />
          {totalPages > 1 ? (
            <div className="flex items-center justify-center gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={page <= 1}
                onClick={() => setPage(page - 1)}
              >
                {t('transactions.previous')}
              </Button>
              <span className="text-sm text-muted-foreground">
                {page} / {totalPages}
              </span>
              <Button
                variant="outline"
                size="sm"
                disabled={page >= totalPages}
                onClick={() => setPage(page + 1)}
              >
                {t('transactions.next')}
              </Button>
            </div>
          ) : (
            <div className="hidden sm:block" />
          )}

          <div className="flex items-center gap-2">
            <span className="text-xs text-muted-foreground">{t('common.rowsPerPage', 'Rows per page')}</span>
            <Select
              value={String(limit)}
              onValueChange={(val) => {
                const nextLimit = Number(val)
                setLimit(nextLimit)
                setPage(1)
                try {
                  localStorage.setItem('talisma.transactions.pageSize', String(nextLimit))
                } catch {
                  // ignored
                }
              }}
            >
              <SelectTrigger className="w-[70px] h-8 text-xs">
                <SelectValue placeholder={limit} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="10">10</SelectItem>
                <SelectItem value="20">20</SelectItem>
                <SelectItem value="50">50</SelectItem>
                <SelectItem value="100">100</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>
      )}

      {/* Bulk Action Bar — aligned with the main content area: clears the
          fixed sidebar on lg+ and matches the page's max-w-7xl + p-6 wrapper
          so the bar visually sits over the transactions list, not the
          full viewport. */}
      <div
        className={`fixed bottom-0 left-0 right-0 lg:left-60 z-50 transition-transform duration-200 ease-out ${selectedIds.size > 0 ? 'translate-y-0' : 'translate-y-full'}`}
      >
        <div className="mx-auto max-w-7xl px-3 md:px-6 pb-[max(1rem,env(safe-area-inset-bottom))] md:pb-6">
          {/* All bulk actions stay visible; on narrow screens the bar wraps
              to extra lines instead of hiding anything or scrolling. env()
              bottom padding keeps the device gesture bar from overlapping. */}
          <div className="flex items-center flex-wrap gap-1.5 bg-card border border-border shadow-xl rounded-2xl p-2">
            {/* Selection count + net total — stacked vertically so the
                sum (issue #185) adds no horizontal width to an already
                crowded bar. The sum is hidden below sm where only the
                count pill shows. */}
            <div className="flex items-center gap-2.5 pl-3 pr-4 whitespace-nowrap shrink-0">
              <span className="inline-flex items-center justify-center size-6 rounded-full bg-primary/10 text-primary text-xs font-semibold shrink-0">
                {selectedIds.size}
              </span>
              <div className="hidden sm:flex flex-col leading-tight">
                <span className="text-[11px] font-medium text-muted-foreground">
                  {t('transactions.selected')}
                </span>
                <span
                  className={`text-sm font-bold tabular-nums ${
                    selectedNet >= 0 ? 'text-emerald-600' : 'text-rose-500'
                  }`}
                >
                  {mask(
                    `${selectedNet >= 0 ? '+' : '−'}${formatCurrency(
                      Math.abs(selectedNet),
                      userCurrency,
                      locale,
                    )}`,
                  )}
                </span>
              </div>
            </div>

            <div className="w-px bg-border/60 self-stretch" />

            {/* Categorize — fires on selection, no separate Apply button */}
            <CategorySelect
              key={bulkCategory}
              value={bulkCategory}
              onChange={(next) => {
                setBulkCategory(next)
                if (next) {
                  bulkCategorizeMutation.mutate({ ids: Array.from(selectedIds), categoryId: next })
                }
              }}
              categories={categoriesList ?? []}
              groups={categoryGroupsList ?? []}
              placeholder={t('transactions.selectCategory')}
              disabled={bulkCategorizeMutation.isPending}
              className="w-44 md:w-56 h-auto py-2 border-transparent bg-transparent hover:bg-muted/60 focus:bg-muted/60 focus-visible:ring-0 shrink-0"
              contentProps={{ side: 'top', sideOffset: 8 }}
            />

            <div className="w-px bg-border/60 self-stretch" />

            {/* Add to group — opens a dialog to configure share type and
                members. Mirrors the per-tx splits options (issue #156). */}
            <Button
              size="sm"
              variant="ghost"
              disabled={bulkAddToGroupMutation.isPending}
              onClick={() => setBulkAddToGroupOpen(true)}
              title={t('transactions.addToGroup')}
              className="h-8 px-3 shrink-0 text-sm"
            >
              <Users size={15} className="lg:mr-1.5" />
              <span className="hidden lg:inline">{t('transactions.addToGroup')}</span>
            </Button>

            <div className="w-px bg-border/60 self-stretch" />

            {/* Add tags inline */}
            <div className="flex items-center gap-1 px-1 shrink-0">
              <input
                type="text"
                value={bulkTagInput}
                onChange={(e) => setBulkTagInput(e.target.value)}
                placeholder={t('transactions.addTagsPlaceholder', '#tag…')}
                className="rounded-lg px-2.5 py-2 text-sm bg-transparent text-foreground placeholder:text-muted-foreground/70 focus:outline-none focus:bg-muted/60 w-28 md:w-40"
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && bulkTagInput.trim()) {
                    e.preventDefault()
                    const tagList = bulkTagInput.trim().split(/[\s,]+/).filter(Boolean)
                    bulkAddTagsMutation.mutate({ ids: Array.from(selectedIds), tags: tagList })
                  }
                }}
              />
              <Button
                size="sm"
                variant="ghost"
                disabled={!bulkTagInput.trim() || bulkAddTagsMutation.isPending}
                onClick={() => {
                  const tagList = bulkTagInput.trim().split(/[\s,]+/).filter(Boolean)
                  if (tagList.length === 0) return
                  bulkAddTagsMutation.mutate({ ids: Array.from(selectedIds), tags: tagList })
                }}
                className="h-8 w-8 px-0 shrink-0"
                title={t('transactions.bulkAddTags', 'Add tags')}
              >
                <Check size={15} />
              </Button>
              <Button
                size="sm"
                variant="ghost"
                disabled={!bulkTagInput.trim() || bulkRemoveTagsMutation.isPending}
                onClick={() => {
                  const tagList = bulkTagInput.trim().split(/[\s,]+/).filter(Boolean)
                  if (tagList.length === 0) return
                  bulkRemoveTagsMutation.mutate({ ids: Array.from(selectedIds), tags: tagList })
                }}
                className="h-8 w-8 px-0 shrink-0"
                title={t('transactions.bulkRemoveTags', 'Remove tags')}
              >
                <X size={15} />
              </Button>
            </div>

            <div className="w-px bg-border/60 self-stretch" />

            {/* Link transfer */}
            <Button
              size="sm"
              variant="ghost"
              disabled={!canOpenLinkDialog}
              title={linkDisabledTooltip ?? t('transactions.linkAsTransfer')}
              onClick={() => setLinkTransferDialogOpen(true)}
              className="h-8 px-3 shrink-0 text-sm"
            >
              <ArrowLeftRight size={15} className="mr-1.5" />
              <span className="hidden lg:inline">{t('transactions.linkAsTransfer')}</span>
            </Button>

            <div className="w-px bg-border/60 self-stretch" />

            {/* Create Rule — only when exactly one non-shared transaction is selected */}
            {selectedIds.size === 1 && (() => {
              const selectedTx = filteredItems.find(tx => selectedIds.has(tx.id))
              if (!selectedTx || selectedTx.is_shared) return null
              return (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => handleCreateRuleFromTransaction(selectedTx)}
                  className="h-8 px-3 shrink-0 text-sm"
                  title={t('transactions.createRule')}
                >
                  <SlidersHorizontal size={15} className="lg:mr-1.5" />
                  <span className="hidden lg:inline">{t('transactions.createRule')}</span>
                </Button>
              )
            })()}

            <div className="ml-auto" />

            {/* Close */}
            <button
              onClick={() => { setSelectedIds(new Set()); setBulkCategory(''); setBulkTagInput('') }}
              className="text-muted-foreground hover:text-foreground p-2 shrink-0 self-center rounded-lg hover:bg-muted/60"
              title={t('common.close', 'Close')}
            >
              <X size={16} />
            </button>
          </div>
        </div>
      </div>

      {/* Bulk Add-to-Group Dialog */}
      <BulkAddToGroupDialog
        open={bulkAddToGroupOpen}
        onClose={() => setBulkAddToGroupOpen(false)}
        selectedCount={selectedIds.size}
        onSubmit={(payload) =>
          bulkAddToGroupMutation.mutate({ ids: Array.from(selectedIds), payload })
        }
        isPending={bulkAddToGroupMutation.isPending}
      />

      {/* Link Transfer Dialog */}
      <LinkTransferDialog
        open={linkTransferDialogOpen}
        onClose={() => setLinkTransferDialogOpen(false)}
        debit={linkablePair?.debit ?? null}
        credit={linkablePair?.credit ?? null}
        anchor={linkAnchor}
        accounts={accountsList ?? []}
        onConfirm={(debitId, creditId) => {
          linkTransferMutation.mutate([debitId, creditId])
        }}
        onCreateCounterpart={(anchorId, toAccountId) => {
          createCounterpartMutation.mutate({ anchorId, toAccountId })
        }}
        loading={linkTransferMutation.isPending || createCounterpartMutation.isPending}
      />

      {/* Transfer Dialog */}
      <TransferDialog
        open={transferDialogOpen}
        onClose={() => setTransferDialogOpen(false)}
        accounts={accountsList ?? []}
        onSave={(data) => transferMutation.mutate(data)}
        loading={transferMutation.isPending}
      />

      {/* Add/Edit Dialog */}
      <TransactionDialog
        open={dialogOpen}
        onClose={() => {
          setDialogOpen(false)
          setEditingTx(null)
          setDuplicateDraft(null)
          // Drop any prior mutation error so reopening the dialog
          // doesn't surface a stale message (issue #155).
          createMutation.reset()
          createInstallmentsMutation.reset()
          updateMutation.reset()
        }}
        transaction={editingTx}
        duplicateDraft={duplicateDraft}
        formResetKey={formResetKey}
        categories={categoriesList ?? []}
        categoryGroups={categoryGroupsList ?? []}
        accounts={accountsList ?? []}
        recurringMatch={editingTx ? recurringList?.find(r => r.description === editingTx.description && r.type === editingTx.type) : undefined}
        onSave={handleTransactionSave}
        onCreateInstallments={handleCreateInstallments}
        onDelete={editingTx ? () => deleteMutation.mutate(editingTx.id) : undefined}
        onUnlinkTransfer={(pairId) => unlinkTransferMutation.mutate(pairId)}
        onIgnoreChanged={invalidateAfterTxMutation}
        onCreateRule={(tx) => {
          setDialogOpen(false)
          setEditingTx(null)
          handleCreateRuleFromTransaction(tx)
        }}
        loading={createMutation.isPending || createInstallmentsMutation.isPending || updateMutation.isPending || deleteMutation.isPending || unlinkTransferMutation.isPending}
        error={createMutation.error || createInstallmentsMutation.error || updateMutation.error ? extractApiError(createMutation.error || createInstallmentsMutation.error || updateMutation.error) : null}
        isSynced={editingTx?.source === 'sync'}
      />

      {/* Create Rule from Transaction Dialog */}
      <RuleDialog
        key={createRuleOpen ? 'rule-open' : 'rule-closed'}
        open={createRuleOpen}
        onClose={() => { setCreateRuleOpen(false); setCreateRuleInitialData(undefined) }}
        rule={null}
        categories={categoriesList ?? []}
        categoryGroups={categoryGroupsList ?? []}
        accounts={accountsList ?? []}
        payees={payeesList ?? []}
        onSave={(data) => createRuleMutation.mutate(data as Omit<Rule, 'id' | 'user_id'>)}
        loading={createRuleMutation.isPending}
        initialData={createRuleInitialData}
      />
    </div>
  )
}
